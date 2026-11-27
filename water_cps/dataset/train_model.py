"""모델 학습: 약품 주입률 예측 모델을 훈련하고 저장한다.

2단계 구조 (도메인 문서 4-5절):
  1단계 — 유형 분류: 입력 특성 → water_type (RandomForest 분류기)
  2단계 — 유형별 회귀: 입력 특성 → 자-테스트 필요 주입률 (유형마다 별도 모델)

학습 목표는 운영자 설정값이 아니라 자-테스트 값(jar_test_dose_mg_per_l)이다.
운영자 설정값은 하루 한 번만 바뀌어서 지금 탁도와 맞지 않는 때가 많다. 그것을 따라 배우면
모델도 늦게 반응하는 운영자를 흉내 낼 뿐이다.

입력 특성: 측정 탁도, log(측정 탁도), 탁도 변화율(1시간), 조류 보정값 (ai_model.features)
입력: dataset/train.csv (build_dataset.py 로 생성)
출력: dataset/models/
  - scaler.pkl         입력 정규화 (분류기·회귀 공용)
  - classifier.pkl     유형 분류기
  - model_<유형>.pkl   유형별 회귀 모델 (데이터가 있는 유형만)
  - guard.json         AI1 학습 범위, AI2 불확실성 기준 (ai_model.make_guard)
  - metrics.json       평가 지표

검증 분할: 유형 비율을 지켜 무작위로 20%를 떼어 둔다(stratified). 시간 순서로 뒤쪽을 떼면
뒤쪽 6일이 평상시뿐이라 호우·조류 모델을 검증할 수 없다. 이 모델은 시간 정보를 입력으로
쓰지 않으므로 무작위 분할이 시간 순서를 새어 나가게 하지 않는다.

실행:
    cd ~/water_cps
    venv/bin/python dataset/train_model.py
"""

import csv
import json
import math
import os
import pickle
import sys
from collections import Counter

WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WATER_CPS_DIR)

try:
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.metrics import (
        accuracy_score, classification_report, mean_absolute_error, mean_squared_error,
    )
    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler
except ImportError:
    print("scikit-learn 이 없습니다. 설치 후 다시 실행하세요:")
    print("  venv/bin/pip install scikit-learn")
    sys.exit(1)

import ai_model  # noqa: E402

TRAIN_CSV = os.path.join(WATER_CPS_DIR, 'dataset', 'train.csv')
MODEL_DIR = ai_model.MODEL_DIR

# 5시간 지연 큐가 처음 값으로 차 있는 구간 (600걸음 = 공정 5시간)
WARMUP_STEPS = 600
RANDOM_SEED = 42


def load_data(path: str):
    """CSV → (X, y_target, y_type, y_required, y_operator)."""
    X, y_target, y_type, y_required, y_operator = [], [], [], [], []
    with open(path) as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i < WARMUP_STEPS:
                continue
            # 모델은 탁도계가 읽은 값(측정 오차 포함)만 본다. 참값은 평가에만 쓴다
            X.append(ai_model.features(float(row['measured_turbidity_ntu']),
                                       float(row['turbidity_trend']),
                                       float(row['algae_extra_mg_per_l'])))
            y_target.append(float(row['jar_test_dose_mg_per_l']))
            y_type.append(row['water_type'])
            y_required.append(float(row['required_dose_mg_per_l']))
            y_operator.append(float(row['dose_setpoint_mg_per_l']))
    print(f"로드: {len(X):,}행 (워밍업 {WARMUP_STEPS}걸음 제외)")
    print("유형별: " + ', '.join(f"{k} {v:,}" for k, v in sorted(Counter(y_type).items())))
    return X, y_target, y_type, y_required, y_operator


def _pick(values, indexes):
    return [values[i] for i in indexes]


def train(csv_path: str = TRAIN_CSV, model_dir: str = MODEL_DIR):
    os.makedirs(model_dir, exist_ok=True)

    print("=" * 50)
    print("1. 데이터 로드")
    print("=" * 50)
    X, y_target, y_type, y_required, y_operator = load_data(csv_path)

    all_idx = list(range(len(X)))
    idx_train, idx_val = train_test_split(
        all_idx, test_size=0.2, random_state=RANDOM_SEED, stratify=y_type,
    )
    print(f"훈련: {len(idx_train):,}행 / 검증: {len(idx_val):,}행 (유형 비율 유지 무작위 분할)")

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(_pick(X, idx_train))
    X_val_s = scaler.transform(_pick(X, idx_val))
    type_train, type_val = _pick(y_type, idx_train), _pick(y_type, idx_val)

    metrics = {'train_rows': len(idx_train), 'val_rows': len(idx_val),
               'target': 'jar_test_dose_mg_per_l', 'safety_margin': ai_model.SAFETY_MARGIN}

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("2. 유형 분류기 훈련 (1단계)")
    print("=" * 50)
    clf = RandomForestClassifier(n_estimators=100, random_state=RANDOM_SEED, n_jobs=-1)
    clf.fit(X_train_s, type_train)
    type_pred = clf.predict(X_val_s)
    clf_acc = accuracy_score(type_val, type_pred)
    print(f"분류 정확도: {clf_acc:.4f}")
    print(classification_report(type_val, type_pred, zero_division=0))
    metrics['classifier_accuracy'] = round(clf_acc, 4)

    # -----------------------------------------------------------------------
    print("=" * 50)
    print("3. 유형별 회귀 모델 훈련 (2단계)")
    print("=" * 50)
    regressors, reg_metrics = {}, {}
    for label in sorted(set(type_train)):
        tr = [k for k, t in enumerate(type_train) if t == label]
        va = [k for k, t in enumerate(type_val) if t == label]
        if len(tr) < 10:
            print(f"  [{label}] 데이터 부족 ({len(tr)}행) — 건너뜀")
            continue

        # 평상시는 탁도 범위가 좁아 선형으로 충분하다. 나머지는 범위가 넓어 비선형 모델을 쓴다.
        reg = Ridge(alpha=1.0) if label == 'normal' else RandomForestRegressor(
            n_estimators=100, min_samples_leaf=5, random_state=RANDOM_SEED, n_jobs=-1)
        reg.fit(_pick(X_train_s, tr), _pick(_pick(y_target, idx_train), tr))
        regressors[label] = reg

        pred = reg.predict(_pick(X_val_s, va))
        truth = _pick(_pick(y_required, idx_val), va)
        mae = mean_absolute_error(truth, pred)
        rmse = math.sqrt(mean_squared_error(truth, pred))
        print(f"  [{label}] 훈련 {len(tr):,}행 / 검증 {len(va):,}행 | "
              f"참 필요 주입률 대비 MAE={mae:.3f}, RMSE={rmse:.3f} mg/L")
        reg_metrics[label] = {'train_rows': len(tr), 'val_rows': len(va),
                              'mae_vs_required': round(mae, 4), 'rmse_vs_required': round(rmse, 4)}
    metrics['regressors'] = reg_metrics

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("3-1. AI1·AI2 기준 정하기")
    print("=" * 50)
    spreads = []
    for label, reg in regressors.items():
        tr = [k for k, t in enumerate(type_train) if t == label]
        spreads += list(ai_model.tree_spread(reg, _pick(X_train_s, tr)))
    guard = ai_model.make_guard(_pick(X, idx_train), spreads)
    print("  AI1 범위: " + ', '.join(f"{n} {lo:.3g}~{hi:.3g}" for n, lo, hi in
                                     zip(guard['feature_names'], guard['low'], guard['high'])))
    print(f"  AI2 유형 확신 기준 {guard['type_confidence_min']}, "
          f"나무 편차 기준 {guard['tree_spread_limit']:.3f} mg/L")
    val_results = ai_model.assess_batch(_pick(X, idx_val), scaler, clf, regressors, guard)
    rate = {flag: sum(1 for r in val_results if flag in r['flags']) / len(val_results)
            for flag in ('AI1_NO_DECREASE', 'AI2_TYPE', 'AI2_SPREAD')}
    print("  검증 데이터에서 걸린 비율 (평소 상황이라 낮아야 한다): "
          + ', '.join(f"{k} {v*100:.2f}%" for k, v in rate.items()))
    metrics['guard_rates_on_validation'] = {k: round(v, 4) for k, v in rate.items()}

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("4. 운영자와 비교 (검증 데이터, 참 필요 주입률 대비)")
    print("=" * 50)
    # 분류기가 고른 유형의 모델로 예측한다. 실제로 쓸 때와 같은 경로다.
    model_pred = []
    for row_s, label in zip(X_val_s, type_pred):
        label = str(label)
        model_pred.append(float(regressors[label].predict([row_s])[0]) if label in regressors
                          else float('nan'))
    required_val = _pick(y_required, idx_val)
    operator_val = _pick(y_operator, idx_val)
    ok = [k for k, p in enumerate(model_pred) if not math.isnan(p)]
    ai_mae = mean_absolute_error(_pick(required_val, ok), _pick(model_pred, ok))
    # 운영자 설정값에는 과주입 여유가 들어 있으므로, 공정하게 비교하려고 AI 쪽에도 같은 여유를 더한 값을 본다
    ai_set = [p + ai_model.SAFETY_MARGIN for p in _pick(model_pred, ok)]
    ai_under = sum(1 for s, r in zip(ai_set, _pick(required_val, ok)) if s < r) / len(ok)
    op_under = sum(1 for s, r in zip(operator_val, required_val) if s < r) / len(required_val)
    op_mae = mean_absolute_error(required_val, operator_val)
    print(f"  AI 필요 주입률 예측 MAE        {ai_mae:.3f} mg/L")
    print(f"  운영자 설정값 − 필요 주입률 MAE {op_mae:.3f} mg/L  (과주입 여유 2 mg/L 포함)")
    print(f"  설정값이 필요 주입률보다 모자란 비율  AI {ai_under*100:.2f}%  운영자 {op_under*100:.2f}%")
    metrics['comparison'] = {
        'ai_mae_vs_required': round(ai_mae, 4),
        'operator_mae_vs_required': round(op_mae, 4),
        'ai_underdose_ratio': round(ai_under, 4),
        'operator_underdose_ratio': round(op_under, 4),
    }

    # -----------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("5. 모델 저장")
    print("=" * 50)

    def save(obj, name):
        path = os.path.join(model_dir, name)
        with open(path, 'wb') as f:
            pickle.dump(obj, f)
        print(f"  저장: {path}")

    with open(os.path.join(model_dir, ai_model.GUARD_FILE), 'w') as f:
        json.dump(guard, f, ensure_ascii=False, indent=2)
    print(f"  저장: {os.path.join(model_dir, ai_model.GUARD_FILE)}")
    save(scaler, 'scaler.pkl')
    save(clf, 'classifier.pkl')
    for label, reg in regressors.items():
        save(reg, f'model_{label}.pkl')
    with open(os.path.join(model_dir, 'metrics.json'), 'w') as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"  저장: {os.path.join(model_dir, 'metrics.json')}")
    return metrics


def predict(raw_turbidity_ntu: float, trend: float = 0.0, algae_extra: float = 0.0,
            model_dir: str = MODEL_DIR) -> dict:
    """저장된 모델과 AI1·AI2 로 판단한다. 테스트용."""
    scaler, clf, regressors = ai_model.load_models(model_dir)
    guard = ai_model.load_guard(model_dir)
    r = ai_model.assess(ai_model.features(raw_turbidity_ntu, trend, algae_extra),
                        scaler, clf, regressors, guard)
    required = r['required']
    return {'water_type': r['water_type'], 'confidence': round(r['confidence'], 2),
            'flags': r['flags'],
            'setpoint_mg_per_l': None if required is None else round(required + ai_model.SAFETY_MARGIN, 3)}


if __name__ == '__main__':
    train()

    print("\n" + "=" * 50)
    print("6. 추론 샘플 테스트")
    print("=" * 50)
    for ntu, trend, algae, desc in [(6.0, 0.0, 0.0, '평상시'), (50.0, 1.3, 0.0, '호우 오름'),
                                    (300.0, 0.0, 0.0, '호우 정점'), (80.0, 0.0, 0.0, '고탁도 머묾'),
                                    (6.0, 0.0, 5.0, '조류'),
                                    (900.0, 0.0, 0.0, '처음 보는 탁도 (AI1)'),
                                    (6.0, 3.0, 0.0, '처음 보는 변화율 (AI1)'),
                                    (80.0, 0.3, 0.0, '호우·고탁도 애매 (AI2)')]:
        r = predict(ntu, trend, algae)
        print(f"  {desc} (탁도={ntu}, 변화율={trend}, 조류={algae}) → 유형={r['water_type']}"
              f"({r['confidence']}), 설정값={r['setpoint_mg_per_l']} mg/L {r['flags'] or ''}")
