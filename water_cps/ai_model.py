"""AI 모델 불러오기와 추론. 학습(train_model.py), 평가(dataset/evaluate_policy.py),
에이전트(ai_agent.py)가 같은 함수를 쓰게 하려고 한곳에 둔다.

minicps를 import하지 않는다. 그래서 MiniCPS 없이도 불러 쓸 수 있다.

모델 (2단계, 도메인 문서 4-5절):
  1단계 — 유형 분류: 입력 특성 → water_type (RandomForest 분류기)
  2단계 — 유형별 회귀: 입력 특성 → 자-테스트 필요 주입률 (유형마다 별도 모델)
설정값 = 예측한 필요 주입률 + SAFETY_MARGIN (운영자가 자-테스트 값에 여유를 얹는 것과 같다)
"""

import math
import os
import pickle

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset', 'models')

# 예측한 필요 주입률 위에 얹는 여유 (mg/L). operator_policy 의 평상시 과주입량과 같은 값.
# 모자라게 넣으면 침전수 탁도가 가파르게 오르고 넘치면 완만하게 오르기 때문에 (설계 문서 4절)
# 약간 넘치게 넣는 편이 안전하다.
SAFETY_MARGIN = 2.0


def features(raw_turbidity_ntu: float, algae_extra: float) -> list:
    """입력 특성: 탁도, log(탁도), 조류 보정값."""
    return [raw_turbidity_ntu, math.log(max(raw_turbidity_ntu, 0.1)), algae_extra]


def load_models(model_dir: str = MODEL_DIR):
    """scaler, classifier, 유형별 regressor를 불러온다."""
    def load(name):
        with open(os.path.join(model_dir, name), 'rb') as f:
            return pickle.load(f)

    scaler = load('scaler.pkl')
    clf = load('classifier.pkl')
    regressors = {}
    for label in clf.classes_:
        label = str(label)
        if os.path.exists(os.path.join(model_dir, f'model_{label}.pkl')):
            regressors[label] = load(f'model_{label}.pkl')
    return scaler, clf, regressors


def predict_required(raw_turbidity_ntu: float, algae_extra: float, scaler, clf, regressors):
    """(예측 필요 주입률 mg/L 또는 None, 유형 레이블). 유형 모델이 없으면 None."""
    x = scaler.transform([features(raw_turbidity_ntu, algae_extra)])
    water_type = str(clf.predict(x)[0])
    if water_type not in regressors:
        return None, water_type
    return float(regressors[water_type].predict(x)[0]), water_type
