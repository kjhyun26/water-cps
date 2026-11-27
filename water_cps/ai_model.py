"""AI 모델 불러오기와 추론. 학습(train_model.py), 평가(dataset/evaluate_policy.py),
에이전트(ai_agent.py)가 같은 함수를 쓰게 하려고 한곳에 둔다.

minicps를 import하지 않는다. 그래서 MiniCPS 없이도 불러 쓸 수 있다.

모델 (2단계, 도메인 문서 4-5절):
  1단계 — 유형 분류: 입력 특성 → water_type (RandomForest 분류기)
  2단계 — 유형별 회귀: 입력 특성 → 자-테스트 필요 주입률 (유형마다 별도 모델)
추천 주입률 = 기본 추천 − 여유 조정 + 자-테스트 보정   (팀 설계 발표 29장, 중간발표 12쪽)
  기본 추천    예측한 필요 주입률 + 운영자 여유(SAFETY_MARGIN). 운영자가 자-테스트 값에 여유를
               얹어 넣는 결정을 5분마다 내는 것
  여유 조정    5시간 뒤 결과로 배워 여유를 줄이는 양. 평소 조건에서만 줄이고 호우·처음 보는
               조건에서는 0. 설계상 운전하며 다시 배우는 "추가 학습"에서 0.5씩 넓히므로
               처음 학습인 지금은 0 (MARGIN_REDUCTION)
  자-테스트 보정  하루 한 번 사람이 한 실험값 − 그때 모델의 예측. 센서에 안 보이는 변화
               (조류 등)를 다음 실험 때까지 메운다

AI 보안 조치 (AI 자신의 약점을 다룬다. 팀 위협 H1~H5·F1과 겹치지 않는다):
  AI1 처음 보는 입력 감지 — 입력 특성이 학습 데이터 범위(조금 넓힌 범위) 밖이면 모델이 모르는
      상황이다. 입력을 범위 끝으로 잘라 예측하고, 에이전트는 그 값과 직전 설정값 중 큰 쪽을 쓴다.
      모르는 상황에서는 주입률을 줄이지 않는다는 뜻이다. 운영자 확인 경보를 낸다
      (팀 설계 "모델이 하는 일: 처음 보는 조건에서는 물러남"). 직전 값을 그대로 붙잡으면
      학습 때보다 빠른 호우에서 크게 모자라게 넣게 되므로 이렇게 했다.
  AI2 예측 불확실성 감시 — 분류기가 유형을 확신하지 못하면(가장 높은 확률 < 기준) 상위 두 유형의
      모델로 각각 예측해 큰 값을 쓴다. 회귀 RandomForest 나무들의 예측이 크게 갈리면 그 편차의
      2배를 더 넣는다. 약품은 모자랄 때가 더 위험하므로 자신 없으면 넉넉한 쪽으로 간다.
  범위와 기준은 train_model.py 가 학습 데이터로 정해 dataset/models/guard.json 에 저장한다.
"""

import collections
import json
import math
import os
import pickle

MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset', 'models')

# 예측한 필요 주입률 위에 얹는 여유 (mg/L). operator_policy 의 평상시 과주입량과 같은 값.
# 모자라게 넣으면 침전수 탁도가 가파르게 오르고 넘치면 완만하게 오르기 때문에 (설계 문서 4절)
# 약간 넘치게 넣는 편이 안전하다.
SAFETY_MARGIN = 2.0
# 여유 조정 (추가 학습에서 0.5씩 넓힌다. 처음 학습 단계는 0)
MARGIN_REDUCTION = 0.0
# 자-테스트 보정 한도 (mg/L). 실험 한 번이 크게 틀려도 추천이 터무니없이 움직이지 않게
JAR_CORRECTION_MIN = -2.0
JAR_CORRECTION_MAX = 10.0
MODEL_INFO_FILE = 'model_info.json'


def jar_correction(jar_test_dose, predicted_required):
    """자-테스트 보정 = 실험값 − 그때 모델의 예측 필요 주입률 (한도 안으로 자름)."""
    return min(max(jar_test_dose - predicted_required, JAR_CORRECTION_MIN), JAR_CORRECTION_MAX)


def recommend(result, correction=0.0):
    """assess 결과로 추천 주입률과 세 부분을 낸다. required 가 None 이면 None."""
    if result['required'] is None:
        return None, {}
    base = result['required'] + SAFETY_MARGIN
    # 여유 조정은 평소 조건(평상시 유형, 보안 조치가 걸리지 않음)에서만
    calm = result['water_type'] == 'normal' and not result['flags']
    reduction = MARGIN_REDUCTION if calm else 0.0
    setpoint = base - reduction + correction
    return setpoint, {'base': round(base, 3), 'margin_reduction': reduction,
                      'jar_correction': round(correction, 3)}


# 탁도 변화율을 보는 창 (공정 초). 호우는 이 창 안에서 크게 오르내리고 고탁도는 천천히 움직인다.
TREND_WINDOW_SECONDS = 3600


def features(raw_turbidity_ntu: float, turbidity_trend: float) -> list:
    """입력 특성: 탁도, log(탁도), 탁도 변화율(1시간 log 비).
    조류는 넣지 않는다. 팀 설계대로 조류는 센서에 직접 보이지 않는 변화로 둔다."""
    return [raw_turbidity_ntu, math.log(max(raw_turbidity_ntu, 0.1)), turbidity_trend]


class TurbidityTrend:
    """측정 탁도 기록을 받아 1시간 전 대비 log 변화량을 낸다.
    기록이 1시간이 안 되면 가장 오래된 값과 비교하고, 하나뿐이면 0을 낸다."""

    def __init__(self, window_seconds: float = TREND_WINDOW_SECONDS):
        self.window = window_seconds
        self.history = collections.deque()   # (공정 시각, 탁도)

    def update(self, sim_time: float, turbidity: float) -> float:
        if self.history and sim_time < self.history[-1][0]:
            self.history.clear()   # 공정 시각이 되돌아가면 (시스템 재시작) 기록을 새로 쌓는다
        self.history.append((sim_time, turbidity))
        while len(self.history) > 1 and self.history[1][0] <= sim_time - self.window:
            self.history.popleft()
        past = self.history[0][1]
        return math.log(max(turbidity, 0.1) / max(past, 0.1))


GUARD_FILE = 'guard.json'
FEATURE_NAMES = ['turbidity', 'log_turbidity', 'turbidity_trend']
# AI1 범위를 학습 데이터 최솟값·최댓값에서 얼마나 넓힐지
OOD_RATIO_MARGIN = 1.2      # 탁도: 최댓값 × 1.2, 최솟값 ÷ 1.2
OOD_TREND_MARGIN = 0.2      # 변화율: ± 0.2
# AI2 기준
TYPE_CONFIDENCE_MIN = 0.8   # 분류기의 가장 높은 확률이 이보다 낮으면 유형을 확신하지 못한 것
SPREAD_FACTOR = 2.0         # 나무 편차가 기준을 넘으면 편차 × 이 값을 더 넣는다


def make_guard(X_train, tree_spreads):
    """학습 데이터로 AI1 범위와 AI2 나무 편차 기준을 정한다."""
    lows = [min(col) for col in zip(*X_train)]
    highs = [max(col) for col in zip(*X_train)]
    low = [lows[0] / OOD_RATIO_MARGIN, math.log(max(lows[0] / OOD_RATIO_MARGIN, 0.1)),
           lows[2] - OOD_TREND_MARGIN]
    high = [highs[0] * OOD_RATIO_MARGIN, math.log(highs[0] * OOD_RATIO_MARGIN),
            highs[2] + OOD_TREND_MARGIN]
    spreads = sorted(tree_spreads)
    # 학습 데이터에서 나온 편차의 99.9 백분위수보다 20% 크면 평소와 다르게 갈린 것으로 본다
    spread_limit = spreads[int(len(spreads) * 0.999)] * 1.2 if spreads else float('inf')
    return {'feature_names': FEATURE_NAMES, 'low': low, 'high': high,
            'type_confidence_min': TYPE_CONFIDENCE_MIN, 'tree_spread_limit': spread_limit,
            'spread_factor': SPREAD_FACTOR}


def tree_spread(regressor, x):
    """RandomForest 나무들 예측의 표준편차 (행마다). 나무가 없는 모델(Ridge)은 0."""
    import numpy as np
    if not hasattr(regressor, 'estimators_'):
        return np.zeros(len(x))
    return np.stack([tree.predict(x) for tree in regressor.estimators_]).std(axis=0)


def clip_to_range(row, guard):
    """AI1: 입력을 학습 범위 끝으로 자른다. log(탁도)는 자른 탁도로 다시 계산한다."""
    clipped = [min(max(value, lo), hi) for value, lo, hi in zip(row, guard['low'], guard['high'])]
    clipped[1] = math.log(max(clipped[0], 0.1))
    return clipped


def out_of_range(row, guard):
    """AI1: 학습 범위 밖인 특성 목록 [(이름, 값, 하한, 상한)]. 비어 있으면 범위 안."""
    return [(name, value, lo, hi)
            for name, value, lo, hi in zip(guard['feature_names'], row, guard['low'], guard['high'])
            if not lo <= value <= hi]


def assess_batch(rows, scaler, clf, regressors, guard):
    """여러 입력을 한 번에 판단한다. 행마다 dict:
    required (예측 필요 주입률. 유형 모델이 없으면 None), water_type, confidence, spread,
    flags, ood (AI1 범위 밖 특성 목록. 있으면 required 는 잘린 입력으로 낸 값이고
    쓰는 쪽은 직전 설정값보다 줄이지 않아야 한다)"""
    import numpy as np
    oods = [out_of_range(row, guard) for row in rows]
    rows = [clip_to_range(row, guard) if ood else row for row, ood in zip(rows, oods)]
    x = scaler.transform(rows)
    proba = clf.predict_proba(x)
    classes = [str(c) for c in clf.classes_]
    order = np.argsort(-proba, axis=1)

    # 쓰일 수 있는 유형(행마다 상위 두 유형)의 모델만 한 번에 묶어 예측한다
    needed = {classes[j] for row_order in order for j in row_order[:2]} & set(regressors)
    predictions, spreads = {}, {}
    for label in needed:
        predictions[label] = regressors[label].predict(x)
        spreads[label] = tree_spread(regressors[label], x)

    results = []
    for i, row in enumerate(rows):
        ood = oods[i]
        top, second = classes[order[i][0]], classes[order[i][1]] if len(classes) > 1 else None
        confidence = float(proba[i][order[i][0]])
        result = {'water_type': top, 'confidence': confidence, 'spread': 0.0,
                  'flags': [], 'ood': ood, 'required': None}
        if ood:
            result['flags'].append('AI1_NO_DECREASE')
        if top not in predictions:
            result['flags'].append('F1_NO_MODEL_HOLD')
            results.append(result)
            continue
        required = float(predictions[top][i])
        spread = float(spreads[top][i])
        # AI2-1: 유형을 확신하지 못하면 두 번째 유형의 예측과 비교해 큰 쪽
        if confidence < guard['type_confidence_min'] and second in predictions:
            other = float(predictions[second][i])
            if other > required:
                required = other
                spread = max(spread, float(spreads[second][i]))
            result['flags'].append('AI2_TYPE')
        # AI2-2: 나무들의 예측이 평소보다 크게 갈리면 넉넉하게
        if spread > guard['tree_spread_limit']:
            required += guard['spread_factor'] * spread
            result['flags'].append('AI2_SPREAD')
        result['required'] = required
        result['spread'] = spread
        results.append(result)
    return results


def assess(row, scaler, clf, regressors, guard):
    return assess_batch([row], scaler, clf, regressors, guard)[0]


MODEL_FILES_GLOB = ('scaler.pkl', 'classifier.pkl', 'model_*.pkl', GUARD_FILE)


def model_hash(model_dir: str = MODEL_DIR):
    """모델 파일 전체의 sha256. 예측값 패킷의 model_hash (팀 설계 H5: 승인 목록 대조용)."""
    import glob
    import hashlib
    digest = hashlib.sha256()
    paths = sorted({p for pattern in MODEL_FILES_GLOB for p in glob.glob(os.path.join(model_dir, pattern))})
    for path in paths:
        digest.update(os.path.basename(path).encode())
        with open(path, 'rb') as f:
            digest.update(f.read())
    return digest.hexdigest()


def load_model_info(model_dir: str = MODEL_DIR):
    try:
        with open(os.path.join(model_dir, MODEL_INFO_FILE)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {'model_version': 'unknown'}


def load_guard(model_dir: str = MODEL_DIR):
    with open(os.path.join(model_dir, GUARD_FILE)) as f:
        return json.load(f)


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
    # 학습은 여러 코어로 했지만, 한 줄씩 예측할 때는 스레드를 띄우는 비용이 더 크다
    for model in [clf, *regressors.values()]:
        if hasattr(model, 'n_jobs'):
            model.n_jobs = 1
    return scaler, clf, regressors


def predict_required(raw_turbidity_ntu: float, turbidity_trend: float, scaler, clf, regressors):
    """AI1·AI2 없이 모델만 쓴 예측 (비교용). (예측 필요 주입률 또는 None, 유형 레이블)."""
    x = scaler.transform([features(raw_turbidity_ntu, turbidity_trend)])
    water_type = str(clf.predict(x)[0])
    if water_type not in regressors:
        return None, water_type
    return float(regressors[water_type].predict(x)[0]), water_type
