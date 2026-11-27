"""운영자 정책과 AI 정책을 같은 30일 원수 시나리오로 돌려 결과를 비교한다.

원수 시나리오는 학습 때와 같고, 탁도계 측정 오차는 다른 난수로 새로 만든다.
공정 수식은 build_dataset.py 와 같다 (MiniCPS 없이 빠르게 계산).
AI 쪽은 ai_agent.py 와 같은 판단(AI1·AI2·F1)을 거치고, 공정 5분마다 추천을 새로 낸다.

비교하는 정책:
    운영자              매일 9시 자-테스트 + 여유 2, 탁도 100 NTU 넘으면 비상 주입
    기준값              조견표(baseline.py) + 매일 9시 자-테스트 갱신. 폴백 때 쓰는 값
    AI (보정 없음)       기본 추천만 (탁도·변화율로 예측 + 여유 2)
    AI (자-테스트 보정)   기본 추천 − 여유 조정 + 매일 9시 자-테스트 보정 (팀 설계 공식)

비교 항목:
    침전수 탁도가 1.0 NTU를 넘은 시간과 최댓값
    그중 조류 기간에 넣은 물에서 생긴 초과 시간 (AI가 볼 수 없는 변화. 한계 확인용)
    PLC3 경보 횟수 (1.0 NTU를 공정 30분 넘게 연속 초과한 횟수)
    약품 사용량 (t)

실행:
    cd ~/water_cps
    venv/bin/python dataset/evaluate_policy.py
"""

import collections
import os
import random
import sys

WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WATER_CPS_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai_model  # noqa: E402
import baseline  # noqa: E402
from build_dataset import _required_dose, _settled_turbidity, measure  # noqa: E402
from config import (  # noqa: E402
    CHEMICAL_PUMP_MAX, INITIAL_DOSE_SETPOINT, INITIAL_RAW_TURBIDITY, PROCESS_STEP_SECONDS,
    PRODUCTION_FLOW, SETTLING_DELAY_SECONDS, TURBIDITY_ALARM_MINUTES, TURBIDITY_LIMIT,
)
from operator_policy import OperatorPolicy  # noqa: E402
from scenarios import default_scenario, raw_conditions  # noqa: E402

DAYS = 30
QUEUE_LENGTH = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS
STEP_HOURS = PROCESS_STEP_SECONDS / 3600
PREDICTION_STEPS = 300 // PROCESS_STEP_SECONDS   # 공정 5분마다 추천
JAR_TEST_HOUR = 9


def is_jar_test_step(i):
    t = i * PROCESS_STEP_SECONDS
    return t % 86400 == JAR_TEST_HOUR * 3600


def ai_setpoints(measured, trends, jar_tests, use_jar, scaler, clf, regressors):
    """걸음마다 AI 추천값. 모델 판단은 한 번에 묶어 계산하고, 5분 주기·자-테스트 보정·AI1·F1 은
    ai_agent.py 와 같은 순서로 차례로 적용한다."""
    rows = [ai_model.features(m, t) for m, t in zip(measured, trends)]
    results = ai_model.assess_batch(rows, scaler, clf, regressors, ai_model.load_guard())
    setpoints, previous, correction = [], INITIAL_DOSE_SETPOINT, 0.0
    for i, r in enumerate(results):
        if use_jar and i in jar_tests and r['required'] is not None:
            correction = ai_model.jar_correction(jar_tests[i], r['required'])
        if i % PREDICTION_STEPS == 0:
            value, _ = ai_model.recommend(r, correction)
            if value is not None:
                if r['ood']:
                    value = max(value, previous)   # AI1: 처음 보는 입력에서는 줄이지 않는다
                previous = min(max(value, 5.0), 50.0)   # F1
        setpoints.append(previous)
    flagged = {f: sum(1 for k, r in enumerate(results) if k % PREDICTION_STEPS == 0 and f in r['flags'])
               for f in ('AI1_NO_DECREASE', 'AI2_TYPE', 'AI2_SPREAD')}
    return setpoints, flagged


def baseline_setpoints(measured, jar_tests, table):
    """기준값: 조견표 + 매일 자-테스트 갱신. AI 출력은 쓰지 않는다."""
    setpoints, correction = [], 0.0
    for i, m in enumerate(measured):
        if i in jar_tests:
            correction = baseline.jar_correction(jar_tests[i], m, table)
        setpoints.append(baseline.baseline_dose(m, table, correction))
    return setpoints


def simulate(conditions, setpoints):
    alarm_steps = TURBIDITY_ALARM_MINUTES * 60 // PROCESS_STEP_SECONDS
    # 5시간 지연 큐: (실제 주입률 − 필요 주입률, 그 물의 유형)
    queue = collections.deque(
        [(INITIAL_DOSE_SETPOINT - _required_dose(INITIAL_RAW_TURBIDITY), 'normal')] * QUEUE_LENGTH)
    over_steps = algae_over_steps = over_run = alarms = 0
    max_settled = 0.0
    chemical_kg = 0.0
    for (ntu, algae, label), setpoint in zip(conditions, setpoints):
        command = min(max(PRODUCTION_FLOW * setpoint / 1000, 0.0), CHEMICAL_PUMP_MAX)
        actual = command * 1000 / PRODUCTION_FLOW
        chemical_kg += command * PROCESS_STEP_SECONDS / 3600
        queue.append((actual - (_required_dose(ntu) + algae), label))
        difference, dosed_label = queue.popleft()
        settled = _settled_turbidity(difference)
        max_settled = max(max_settled, settled)
        if settled > TURBIDITY_LIMIT:
            over_steps += 1
            algae_over_steps += dosed_label == 'algae'
            over_run += 1
            if over_run == alarm_steps + 1:
                alarms += 1
        else:
            over_run = 0
    return {'over_hours': over_steps * STEP_HOURS, 'algae_over_hours': algae_over_steps * STEP_HOURS,
            'max_settled': max_settled, 'alarms': alarms, 'chemical_t': chemical_kg / 1000}


def main():
    scenario = default_scenario()
    steps = DAYS * 86400 // PROCESS_STEP_SECONDS
    conditions = [raw_conditions(i * PROCESS_STEP_SECONDS, scenario) for i in range(steps)]
    # 운영자와 AI 모두 같은 탁도계 값(측정 오차 포함)을 본다. 학습 데이터와 다른 난수로 만든다
    rng = random.Random(7)
    measured = [measure(ntu, rng) for ntu, _, _ in conditions]
    trend = ai_model.TurbidityTrend()
    trends = [trend.update(i * PROCESS_STEP_SECONDS, m) for i, m in enumerate(measured)]

    # 매일 9시 자-테스트: 그때의 필요 주입률(조류 포함) + 실험 오차
    jar_tests = {i: _required_dose(ntu) + algae + rng.gauss(0.0, 0.5)
                 for i, (ntu, algae, _) in enumerate(conditions) if is_jar_test_step(i)}

    # 운영자는 하루 한 번 자-테스트로 조류까지 반영한 필요량을 잰다
    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(JAR_TEST_HOUR,))
    operator = [policy.setpoint_at(i * PROCESS_STEP_SECONDS, _required_dose(ntu) + algae, m)
                for i, ((ntu, algae, _), m) in enumerate(zip(conditions, measured))]

    scaler, clf, regressors = ai_model.load_models()
    table = baseline.load()
    base = baseline_setpoints(measured, jar_tests, table)
    ai_plain, _ = ai_setpoints(measured, trends, jar_tests, False, scaler, clf, regressors)
    ai_jar, flags = ai_setpoints(measured, trends, jar_tests, True, scaler, clf, regressors)
    results = {
        '운영자 (매일 자-테스트)': simulate(conditions, operator),
        '기준값 (조견표+자-테스트)': simulate(conditions, base),
        'AI (보정 없음)': simulate(conditions, ai_plain),
        'AI (자-테스트 보정)': simulate(conditions, ai_jar),
    }

    print(f"{DAYS}일 시나리오 (호우 3번, 고탁도 1번, 조류 1번) 결과")
    print(f"{'정책':<22}{'1 NTU 초과':>10}{'(조류 기간)':>11}{'최대 침전수 탁도':>16}"
          f"{'PLC3 경보':>10}{'약품 사용량':>12}")
    for name, r in results.items():
        print(f"{name:<22}{r['over_hours']:>8.1f}시간{r['algae_over_hours']:>8.1f}시간"
              f"{r['max_settled']:>13.2f} NTU{r['alarms']:>9}번{r['chemical_t']:>10.1f} t")
    rmse = (sum((a - b) ** 2 for a, b in zip(ai_jar, base)) / len(base)) ** 0.5
    below = sum(1 for a, b in zip(ai_jar, base) if a < b - 3.5) / len(base)
    print(f"AI(자-테스트 보정)와 기준값의 RMSE {rmse:.2f} mg/L, "
          f"기준값보다 3.5 mg/L 넘게 낮은 시간 비율 {below*100:.2f}% (게이트웨이 ⑦ 거부 대상)")
    print("AI 보안 조치가 걸린 추천 수: "
          + ', '.join(f"{k} {v:,}" for k, v in flags.items()) + f" / {steps // PREDICTION_STEPS:,}")
    print("조류는 탁도를 바꾸지 않아 AI 입력에 나타나지 않는다. 자-테스트가 하루 한 번이라")
    print("조류 첫날(다음 실험 전까지)은 AI도 기준값도 모자라게 넣는다 (팀 설계의 남는 위험).")


if __name__ == '__main__':
    main()
