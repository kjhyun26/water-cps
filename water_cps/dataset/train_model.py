"""모델 학습: 약품 주입률 예측 회귀 모델을 훈련하고 저장한다.

2단계 구조 (도메인 문서 4-5절):
  1단계 — 유형 분류: raw_turbidity → water_type (RandomForest 분류기)
  2단계 — 유형별 회귀: raw_turbidity → dose_setpoint (각 유형마다 별도 모델)

입력: dataset/train.csv (build_dataset.py 로 생성)
출력: dataset/models/ 디렉토리
  - classifier.pkl     유형 분류기
  - model_normal.pkl   평상시 회귀 모델
  - model_storm.pkl    호우 회귀 모델
  - model_algae.pkl    조류 회귀 모델
  - model_high_turbidity.pkl  고탁도 회귀 모델 (데이터 있을 때만)
  - scaler.pkl         입력 정규화 (분류기·회귀 공용)
  - metrics.json       평가 지표

실행:
    cd ~/water_cps
    pip install scikit-learn --break-system-packages   # 없으면
    python3 dataset/train_model.py
"""

import json
import math
import os
import pickle
import sys

WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(WATER_CPS_DIR)
sys.path.insert(0, WATER_CPS_DIR)

import csv
from collections import defaultdict

try:
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        mean_absolute_error, mean_squared_error, accuracy_score, classification_report,
    )
    from sklearn.model_selection import train_test_split
except ImportError:
    print("scikit-learn 이 없습니다. 설치 후 다시 실행하세요:")
    print("  pip install scikit-learn --break-system-packages")
    sys.exit(1)

TRAIN_CSV = os.path.join(WATER_CPS_DIR, 'dataset', 'train.csv')
MODEL_DIR = os.path.join(WATER_CPS_DIR, 'dataset', 'models')

# 5시간 delay 이후부터 유효한 데이터 (600걸음 = 18,000초)
WARMUP_STEPS = 600


# ---------------------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------------------
def load_data(path: str):
    """CSV → (X, y_dose, y_type, y_settled) numpy-like 리스트."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < WARMUP_STEPS:
                continue  # 큐 워밍업 기간 제외
            rows.append(row)

    print(f"로드: {len(rows):,}행 (워밍업 {WARMUP_STEPS}걸음 제외)")

    X, y_dose, y_type, y_settled = [], [], [], []
    for r in rows:
        ntu = float(r['raw_turbidity_ntu'])
        algae = float(r['algae_extra_mg_per_l'])
        # 입력 특성: 탁도, log(탁도), 조류 보정값
        X.append([ntu, math.log(max(ntu, 0.1)), algae])
        y_dose.append(float(r['dose_setpoint_mg_per_l']))
        y_type.append(r['water_type'])
        y_settled.append(float(r['settled_turbidity_ntu']))

    return X, y_dose, y_type, y_settled


# ---------------------------------------------------------------------------
# 훈련
# ---------------------------------------------------------------------------
def train(csv_path: str = TRAIN_CSV, model_dir: str = MODEL_DIR):
    os.makedirs(model_dir, exist_ok=True)

    print("=" * 50)
    print("1. 데이터 로드")
    print("=" * 50)
    X, y_dose, y_type, y_settled = load_data(csv_path)

    # 훈련/검증 분할 (시계열이므로 뒷부분을 검증용으로)
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_dose_train, y_dose_val = y_dose[:split], y_dose[split:]
    y_type_train, y_type_val = y_type[:split], y_type[split:]

    print(f"훈련: {len(X_train):,}행 / 검증: {len(X_val):,}행")

    # 정규화
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    metrics = {}

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("2. 유형 분류기 훈련 (1단계)")
    print("=" * 50)
    clf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
    clf.fit(X_train_s, y_type_train)

    y_type_pred = clf.predict(X_val_s)
    clf_acc = accuracy_score(y_type_val, y_type_pred)
    print(f"분류 정확도: {clf_acc:.4f}")
    print(classification_report(y_type_val, y_type_pred))
    metrics['classifier_accuracy'] = round(clf_acc, 4)

    # -----------------------------------------------------------------------
    print("=" * 50)
    print("3. 유형별 회귀 모델 훈련 (2단계)")
    print("=" * 50)

    type_labels = sorted(set(y_type_train))
    regressors = {}
    reg_metrics = {}

    for label in type_labels:
        # 훈련 데이터 필터
        idx_tr = [i for i, t in enumerate(y_type_train) if t == label]
        idx_va = [i for i, t in enumerate(y_type_val) if t == label]

        if len(idx_tr) < 10:
            print(f"  [{label}] 데이터 부족 ({len(idx_tr)}행) — 건너뜀")
            continue

        Xtr = [X_train_s[i] for i in idx_tr]
        ytr = [y_dose_train[i] for i in idx_tr]
        Xva = [X_val_s[i] for i in idx_va] if idx_va else []
        yva = [y_dose_val[i] for i in idx_va] if idx_va else []

        # 유형별 모델 선택
        if label == 'normal':
            reg = Ridge(alpha=1.0)  # 평상시: 선형 충분
        else:
            reg = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)

        reg.fit(Xtr, ytr)
        regressors[label] = reg

        if Xva:
            y_pred = reg.predict(Xva)
            mae = mean_absolute_error(yva, y_pred)
            rmse = math.sqrt(mean_squared_error(yva, y_pred))
            print(f"  [{label}] 훈련 {len(idx_tr):,}행 / 검증 {len(idx_va):,}행 "
                  f"| MAE={mae:.4f} mg/L, RMSE={rmse:.4f} mg/L")
            reg_metrics[label] = {'mae': round(mae, 4), 'rmse': round(rmse, 4),
                                  'train_rows': len(idx_tr), 'val_rows': len(idx_va)}
        else:
            print(f"  [{label}] 훈련 {len(idx_tr):,}행 (검증 데이터 없음)")
            reg_metrics[label] = {'train_rows': len(idx_tr), 'val_rows': 0}

    metrics['regressors'] = reg_metrics

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("4. 모델 저장")
    print("=" * 50)

    def save(obj, name):
        path = os.path.join(model_dir, name)
        with open(path, 'wb') as f:
            pickle.dump(obj, f)
        print(f"  저장: {path}")

    save(scaler, 'scaler.pkl')
    save(clf, 'classifier.pkl')
    for label, reg in regressors.items():
        save(reg, f'model_{label}.pkl')

    metrics_path = os.path.join(model_dir, 'metrics.json')
    with open(metrics_path, 'w') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"  저장: {metrics_path}")

    print("\n완료.")
    return metrics


# ---------------------------------------------------------------------------
# 간단 추론 테스트
# ---------------------------------------------------------------------------
def predict(raw_turbidity_ntu: float, algae_extra: float = 0.0,
            model_dir: str = MODEL_DIR) -> dict:
    """저장된 모델로 주입률을 예측한다. 테스트용."""
    def load(name):
        with open(os.path.join(model_dir, name), 'rb') as f:
            return pickle.load(f)

    scaler = load('scaler.pkl')
    clf = load('classifier.pkl')

    ntu = raw_turbidity_ntu
    x = scaler.transform([[ntu, math.log(max(ntu, 0.1)), algae_extra]])

    water_type = clf.predict(x)[0]
    model_file = f'model_{water_type}.pkl'
    reg = load(model_file)
    dose = reg.predict(x)[0]

    return {
        'water_type': water_type,
        'predicted_dose_mg_per_l': round(dose, 4),
    }


if __name__ == '__main__':
    metrics = train()

    print("\n" + "=" * 50)
    print("5. 추론 샘플 테스트")
    print("=" * 50)
    test_cases = [
        (6.0, 0.0, '평상시'),
        (50.0, 0.0, '고탁도'),
        (300.0, 0.0, '호우'),
        (6.0, 5.0, '조류'),
    ]
    for ntu, algae, desc in test_cases:
        result = predict(ntu, algae)
        print(f"  {desc} (탁도={ntu} NTU, 조류={algae}) → "
              f"유형={result['water_type']}, "
              f"예측 주입률={result['predicted_dose_mg_per_l']} mg/L")
