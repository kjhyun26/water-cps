"""학습 데이터 생성기: 공정 수식을 빠르게 돌려 CSV를 만든다.

minicps 없이 공정 수식(physical_process.py와 같은 식)을 직접 계산한다 (설계 문서 9-2절).
PLC1·PLC2 역할(수위 제어, 유량 × 주입률 ÷ 1000)도 이 스크립트가 대신한다.
상태 표(water_cps.sqlite)는 건드리지 않는다. 그래서 run.py 로 시스템을 돌리는 중에
실행해도 돌고 있는 시스템에 영향이 없다.

실행:
    cd ~/water_cps
    venv/bin/python dataset/build_dataset.py              # 30일치 (기본)
    venv/bin/python dataset/build_dataset.py --days 7     # 7일치
    venv/bin/python dataset/build_dataset.py --out my.csv

출력 CSV 열:
    sim_time_seconds        공정 시각 (초)
    raw_turbidity_ntu       원수 탁도 (참값. 공정이 쓰는 값)
    measured_turbidity_ntu  탁도계가 읽은 원수 탁도 (참값 × 측정 오차). 모델 입력
    turbidity_trend         측정 탁도의 1시간 전 대비 log 변화량. 모델 입력
    algae_extra_mg_per_l    조류 추가 주입률 (보정용)
    water_type              유형 레이블
    dose_setpoint_mg_per_l  운영자 설정 주입률 (하루 한 번 자-테스트로 갱신, 비교용)
    actual_dose_mg_per_l    실제 주입률 (PLC2 계산값)
    required_dose_mg_per_l  필요 주입률 (공정만 아는 참값, 평가 전용)
    jar_test_dose_mg_per_l  그 시각에 자-테스트를 했다면 나왔을 필요 주입률 (참값 + 측정 오차).
                            모델의 학습 목표
    settled_turbidity_ntu   5시간 뒤 침전수 탁도
    dosed_ph                주입 후 pH
    level_m                 착수정 수위
"""

import argparse
import collections
import csv
import math
import os
import random
import sys

# water_cps/ 를 경로에 추가 (dataset/ 한 단계 위)
WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WATER_CPS_DIR)

from config import (  # noqa: E402
    CHEMICAL_PUMP_MAX, INITIAL_DOSE_SETPOINT, INITIAL_INTAKE_PUMP, INITIAL_LEVEL,
    INITIAL_RAW_TURBIDITY, INTAKE_FLOW_HIGH, INTAKE_FLOW_LOW, LEVEL_HIGH, LEVEL_LOW,
    MINIMUM_RAW_TURBIDITY, OVERDOSE_COEFFICIENT, PH_DROP_PER_DOSE, PROCESS_STEP_SECONDS,
    PRODUCTION_FLOW, RAW_PH, REQUIRED_DOSE_BASE, REQUIRED_DOSE_SLOPE, SETTLED_TURBIDITY_BASE,
    SETTLING_DELAY_SECONDS, TANK_AREA, TANK_HEIGHT, UNDERDOSE_COEFFICIENT,
)
from scenarios import default_scenario, raw_conditions  # noqa: E402
import ai_model  # noqa: E402
from operator_policy import OperatorPolicy  # noqa: E402

DEFAULT_DAYS = 30
DEFAULT_OUT = os.path.join(WATER_CPS_DIR, 'dataset', 'train.csv')
QUEUE_LENGTH = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS  # 600

# 자-테스트 측정 오차 (표준편차, mg/L). 비커 실험은 할 때마다 조금씩 다르게 나온다.
JAR_TEST_NOISE_MG_PER_L = 0.5
# 탁도계 측정 오차 (표준편차, 읽은 값의 비율). 온라인 탁도계 정확도 ±2% 수준.
SENSOR_NOISE_RATIO = 0.02
RANDOM_SEED = 42


# ---------------------------------------------------------------------------
# 공정 수식 (physical_process.py와 같은 식 — minicps 없이 쓰기 위해 복사)
# ---------------------------------------------------------------------------
def _required_dose(raw_turbidity: float) -> float:
    return REQUIRED_DOSE_BASE + REQUIRED_DOSE_SLOPE * math.log(
        max(raw_turbidity, MINIMUM_RAW_TURBIDITY)
    )


def _dosed_ph(actual_dose: float) -> float:
    return RAW_PH - PH_DROP_PER_DOSE * actual_dose


def _settled_turbidity(dose_difference: float) -> float:
    if dose_difference < 0:
        return SETTLED_TURBIDITY_BASE + UNDERDOSE_COEFFICIENT * dose_difference ** 2
    return SETTLED_TURBIDITY_BASE + OVERDOSE_COEFFICIENT * dose_difference


def measure(true_ntu: float, rng: random.Random) -> float:
    """탁도계가 읽는 값: 참값에 비율 오차를 곱한다."""
    return max(0.0, true_ntu * (1.0 + rng.gauss(0.0, SENSOR_NOISE_RATIO)))


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
COLUMNS = [
    'sim_time_seconds', 'raw_turbidity_ntu', 'measured_turbidity_ntu', 'turbidity_trend',
    'algae_extra_mg_per_l', 'water_type',
    'dose_setpoint_mg_per_l', 'actual_dose_mg_per_l', 'required_dose_mg_per_l',
    'jar_test_dose_mg_per_l', 'settled_turbidity_ntu', 'dosed_ph', 'level_m',
]


def build(days: int = DEFAULT_DAYS, out_path: str = DEFAULT_OUT, seed: int = RANDOM_SEED) -> None:
    total_steps = days * 86400 // PROCESS_STEP_SECONDS
    print(f"데이터 생성 시작: {days}일 ({total_steps:,} 걸음) → {out_path}")

    rng = random.Random(seed)
    trend = ai_model.TurbidityTrend()
    scenario = default_scenario()
    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(9,))

    level = INITIAL_LEVEL
    intake_pump = INITIAL_INTAKE_PUMP

    initial_diff = INITIAL_DOSE_SETPOINT - _required_dose(INITIAL_RAW_TURBIDITY)
    diff_queue = collections.deque([initial_diff] * QUEUE_LENGTH)
    type_counts = collections.Counter()

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)

        for step_number in range(total_steps):
            sim_time = step_number * PROCESS_STEP_SECONDS

            # 1. 원수 조건
            ntu, algae_extra, water_type = raw_conditions(sim_time, scenario)
            type_counts[water_type] += 1
            measured = measure(ntu, rng)
            turbidity_trend = trend.update(sim_time, measured)

            # 2. 필요 주입률 (조류 보정 포함)과 자-테스트 측정값
            needed = _required_dose(ntu) + algae_extra
            jar_test = needed + rng.gauss(0.0, JAR_TEST_NOISE_MG_PER_L)

            # 3. 운영자 설정값 (하루 한 번 자-테스트, 탁도 급등 시 비상 주입)
            setpoint = policy.setpoint_at(sim_time, needed, measured)

            # 4. PLC2 역할: 유량 × 주입률 ÷ 1000
            chemical_pump_command = min(max(round(PRODUCTION_FLOW * setpoint / 1000, 4), 0.0),
                                        CHEMICAL_PUMP_MAX)

            # 5. 약품·pH (SIS 없음: 항상 열림으로 가정)
            actual_dose = chemical_pump_command * 1000 / PRODUCTION_FLOW
            ph = _dosed_ph(actual_dose)

            # 6. 5시간 지연
            diff_queue.append(actual_dose - needed)
            settled = _settled_turbidity(diff_queue.popleft())

            # 7. 수위 (PLC1 역할)
            intake_flow = INTAKE_FLOW_HIGH if intake_pump == 2 else INTAKE_FLOW_LOW
            level += (intake_flow - PRODUCTION_FLOW) / TANK_AREA * (PROCESS_STEP_SECONDS / 3600)
            level = min(max(level, 0.0), TANK_HEIGHT)
            if level <= LEVEL_LOW:
                intake_pump = 2
            elif level >= LEVEL_HIGH:
                intake_pump = 1

            # 8. CSV 기록
            writer.writerow([
                sim_time, round(ntu, 4), round(measured, 4), round(turbidity_trend, 5),
                round(algae_extra, 4), water_type,
                round(setpoint, 4), round(actual_dose, 4), round(needed, 4),
                round(jar_test, 4), round(settled, 4), round(ph, 4), round(level, 4),
            ])

            # 진행 표시 (10%마다)
            if total_steps >= 10 and (step_number + 1) % (total_steps // 10) == 0:
                pct = (step_number + 1) / total_steps * 100
                print(f"  {pct:5.1f}% — sim_time={sim_time/3600:.1f}h, "
                      f"ntu={ntu:.1f}, type={water_type}, setpoint={setpoint:.2f} mg/L")

    print(f"완료: {total_steps:,}행 → {out_path}")
    print("유형별 걸음 수: " + ', '.join(f"{k} {v:,}" for k, v in sorted(type_counts.items())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='정수장 학습 데이터 생성')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS, help='시뮬레이션 일수 (기본: 30)')
    parser.add_argument('--out', type=str, default=DEFAULT_OUT, help='출력 CSV 경로')
    parser.add_argument('--seed', type=int, default=RANDOM_SEED, help='자-테스트 오차 난수 시드')
    args = parser.parse_args()
    build(days=args.days, out_path=args.out, seed=args.seed)
