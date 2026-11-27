"""운영자 정책과 AI 정책을 같은 30일 원수 시나리오로 돌려 결과를 비교한다.

원수 시나리오는 학습 때와 같고, 탁도계 측정 오차는 다른 난수로 새로 만든다.
공정 수식은 build_dataset.py 와 같다 (MiniCPS 없이 빠르게 계산).
비교 항목:
    침전수 탁도가 1.0 NTU를 넘은 시간 비율과 최댓값
    PLC3 경보 횟수 (1.0 NTU를 공정 30분 넘게 연속 초과한 횟수)
    약품 사용량 (t)

AI 정책은 두 가지로 돌린다.
    AI (조류 정보 있음)  조류 보정값을 안다고 가정 (조류 센서가 있는 경우)
    AI (조류 정보 없음)  지금의 ai_agent.py 와 같다. 조류 보정값을 늘 0으로 넣는다

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
from build_dataset import _required_dose, _settled_turbidity, measure  # noqa: E402
from config import (  # noqa: E402
    CHEMICAL_PUMP_MAX, INITIAL_DOSE_SETPOINT, INITIAL_RAW_TURBIDITY, PROCESS_STEP_SECONDS,
    PRODUCTION_FLOW, SETTLING_DELAY_SECONDS, TURBIDITY_ALARM_MINUTES, TURBIDITY_LIMIT,
)
from operator_policy import OperatorPolicy  # noqa: E402
from scenarios import default_scenario, raw_conditions  # noqa: E402

DAYS = 30
QUEUE_LENGTH = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS


def _ai_setpoints(conditions, measured, trends, use_algae, scaler, clf, regressors):
    """걸음마다 AI 설정값. 입력이 같으면 출력도 같으므로 한 번에 묶어 예측한다."""
    rows = [ai_model.features(m, t, algae if use_algae else 0.0)
            for (_, algae, _), m, t in zip(conditions, measured, trends)]
    # 에이전트와 같은 판단(AI1·AI2·F1). AI1 이면 직전 설정값보다 줄이지 않는다
    results = ai_model.assess_batch(rows, scaler, clf, regressors, ai_model.load_guard())
    setpoints, previous = [], INITIAL_DOSE_SETPOINT
    for r in results:
        if r['required'] is not None:
            value = r['required'] + ai_model.SAFETY_MARGIN
            if r['ood']:
                value = max(value, previous)
            previous = min(max(value, 5.0), 50.0)   # F1
        setpoints.append(previous)
    flagged = {f: sum(1 for r in results if f in r['flags'])
               for f in ('AI1_NO_DECREASE', 'AI2_TYPE', 'AI2_SPREAD')}
    return setpoints, flagged


def simulate(conditions, setpoints):
    alarm_steps = TURBIDITY_ALARM_MINUTES * 60 // PROCESS_STEP_SECONDS
    diff_queue = collections.deque(
        [INITIAL_DOSE_SETPOINT - _required_dose(INITIAL_RAW_TURBIDITY)] * QUEUE_LENGTH)
    over_steps = over_run = alarms = 0
    max_settled = 0.0
    chemical_kg = 0.0
    for (ntu, algae, _), setpoint in zip(conditions, setpoints):
        command = min(max(PRODUCTION_FLOW * setpoint / 1000, 0.0), CHEMICAL_PUMP_MAX)
        actual = command * 1000 / PRODUCTION_FLOW
        chemical_kg += command * PROCESS_STEP_SECONDS / 3600
        diff_queue.append(actual - (_required_dose(ntu) + algae))
        settled = _settled_turbidity(diff_queue.popleft())
        max_settled = max(max_settled, settled)
        if settled > TURBIDITY_LIMIT:
            over_steps += 1
            over_run += 1
            if over_run == alarm_steps + 1:
                alarms += 1
        else:
            over_run = 0
    total = len(conditions)
    return {'over_ratio': over_steps / total, 'max_settled': max_settled,
            'alarms': alarms, 'chemical_t': chemical_kg / 1000}


def main():
    scenario = default_scenario()
    steps = DAYS * 86400 // PROCESS_STEP_SECONDS
    conditions = [raw_conditions(i * PROCESS_STEP_SECONDS, scenario) for i in range(steps)]
    # 운영자와 AI 모두 같은 탁도계 값(측정 오차 포함)을 본다. 학습 데이터와 다른 난수로 만든다
    rng = random.Random(7)
    measured = [measure(ntu, rng) for ntu, _, _ in conditions]
    trend = ai_model.TurbidityTrend()
    trends = [trend.update(i * PROCESS_STEP_SECONDS, m) for i, m in enumerate(measured)]

    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(9,))
    operator = [policy.setpoint_at(i * PROCESS_STEP_SECONDS, _required_dose(ntu) + algae, m)
                for i, ((ntu, algae, _), m) in enumerate(zip(conditions, measured))]

    scaler, clf, regressors = ai_model.load_models()
    with_algae, flags_with = _ai_setpoints(conditions, measured, trends, True, scaler, clf, regressors)
    without_algae, _ = _ai_setpoints(conditions, measured, trends, False, scaler, clf, regressors)
    results = {
        '운영자 (하루 1회 자-테스트)': simulate(conditions, operator),
        'AI (조류 정보 있음)': simulate(conditions, with_algae),
        'AI (조류 정보 없음)': simulate(conditions, without_algae),
    }

    print(f"{DAYS}일 시나리오 (호우 3번, 고탁도 1번, 조류 1번) 결과")
    print(f"{'정책':<22}{'1 NTU 초과 시간':>14}{'최대 침전수 탁도':>16}{'PLC3 경보':>10}{'약품 사용량':>12}")
    for name, r in results.items():
        print(f"{name:<22}{r['over_ratio']*100:>13.2f}%{r['max_settled']:>13.2f} NTU"
              f"{r['alarms']:>9}번{r['chemical_t']:>10.1f} t")
    print("AI (조류 정보 있음)에서 보안 조치가 걸린 걸음 수: "
          + ', '.join(f"{k} {v:,}" for k, v in flags_with.items()) + f" / {steps:,}")


if __name__ == '__main__':
    main()
