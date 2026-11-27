"""학습 데이터 생성기: 공정 시뮬레이션을 빠르게 돌려 CSV를 만든다.

minicps 없이 WaterProcess.step()을 직접 호출한다 (가. 방법, 설계 문서 9-2절).
PLC2 역할(유량 × 주입률 ÷ 1000)을 이 스크립트가 대신한다.

실행:
    cd ~/water_cps
    python3 dataset/build_dataset.py              # 30일치 (기본)
    python3 dataset/build_dataset.py --days 7     # 7일치
    python3 dataset/build_dataset.py --out my.csv

출력 CSV 열:
    sim_time_seconds        공정 시각 (초)
    raw_turbidity_ntu       원수 탁도
    algae_extra_mg_per_l    조류 추가 주입률 (보정용)
    water_type              유형 레이블
    dose_setpoint_mg_per_l  운영자 설정 주입률
    actual_dose_mg_per_l    실제 주입률 (PLC2 계산값)
    required_dose_mg_per_l  필요 주입률 (공정만 아는 값, 평가 전용)
    settled_turbidity_ntu   5시간 뒤 침전수 탁도
    dosed_ph                주입 후 pH
    level_m                 착수정 수위
"""

import argparse
import collections
import csv
import math
import os
import sqlite3
import sys

# water_cps/ 를 경로에 추가 (dataset/ 한 단계 위)
WATER_CPS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(WATER_CPS_DIR)
sys.path.insert(0, WATER_CPS_DIR)

import init  # noqa: E402  — 상태 표 초기화
from config import (  # noqa: E402
    CHEMICAL_PUMP_MAX, INITIAL_DOSE_SETPOINT, INITIAL_INTAKE_PUMP, INITIAL_LEVEL,
    INITIAL_RAW_TURBIDITY, INITIAL_SHUTOFF_VALVE, MINIMUM_RAW_TURBIDITY, OVERDOSE_COEFFICIENT,
    PH_DROP_PER_DOSE, PROCESS_STEP_SECONDS, PRODUCTION_FLOW, RAW_PH, REQUIRED_DOSE_BASE,
    REQUIRED_DOSE_SLOPE, SETTLED_TURBIDITY_BASE, SETTLING_DELAY_SECONDS, STATE,
    TANK_AREA, TANK_HEIGHT, UNDERDOSE_COEFFICIENT, INITIAL_CHEMICAL_PUMP_COMMAND,
)
from scenarios import default_scenario, raw_conditions  # noqa: E402
from operator_policy import OperatorPolicy  # noqa: E402

DEFAULT_DAYS = 30
DEFAULT_OUT = os.path.join(WATER_CPS_DIR, 'dataset', 'train.csv')
QUEUE_LENGTH = SETTLING_DELAY_SECONDS // PROCESS_STEP_SECONDS  # 600


# ---------------------------------------------------------------------------
# 공정 수식 (physical_process.py와 완전히 동일 — minicps 없이 쓰기 위해 복사)
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


# ---------------------------------------------------------------------------
# SQLite 헬퍼
# ---------------------------------------------------------------------------
def _put(conn: sqlite3.Connection, name: str, pid: int, value) -> None:
    # 연결은 build()에서 한 번만 연다. 걸음마다 새로 열면 연결이 닫히지 않고 쌓여
    # 파일 핸들 한도를 넘는다 ("unable to open database file").
    conn.execute(
        'UPDATE water_cps SET value = ? WHERE name = ? AND pid = ?',
        (str(value), name, pid),
    )


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------
COLUMNS = [
    'sim_time_seconds', 'raw_turbidity_ntu', 'algae_extra_mg_per_l', 'water_type',
    'dose_setpoint_mg_per_l', 'actual_dose_mg_per_l', 'required_dose_mg_per_l',
    'settled_turbidity_ntu', 'dosed_ph', 'level_m',
]


def build(days: int = DEFAULT_DAYS, out_path: str = DEFAULT_OUT) -> None:
    total_steps = days * 86400 // PROCESS_STEP_SECONDS
    print(f"데이터 생성 시작: {days}일 ({total_steps:,} 걸음) → {out_path}")

    # 상태 표 초기화
    init.create_state()

    # 시뮬레이션 상태
    scenario = default_scenario()
    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(9,))

    level = INITIAL_LEVEL
    raw_turbidity = INITIAL_RAW_TURBIDITY
    intake_pump = INITIAL_INTAKE_PUMP
    shutoff_valve = INITIAL_SHUTOFF_VALVE
    chemical_pump_command = INITIAL_CHEMICAL_PUMP_COMMAND

    initial_diff = INITIAL_DOSE_SETPOINT - _required_dose(INITIAL_RAW_TURBIDITY)
    diff_queue = collections.deque([initial_diff] * QUEUE_LENGTH)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    conn = sqlite3.connect(STATE['path'])
    try:
        _write_rows(total_steps, out_path, conn, scenario, policy, level, intake_pump,
                    chemical_pump_command, diff_queue)
        conn.commit()
    finally:
        conn.close()

    print(f"완료: {total_steps:,}행 → {out_path}")


def _write_rows(total_steps, out_path, conn, scenario, policy, level, intake_pump,
                chemical_pump_command, diff_queue):
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)

        for step_number in range(total_steps):
            sim_time = step_number * PROCESS_STEP_SECONDS

            # 1. 원수 조건 계산
            ntu, algae_extra, water_type = raw_conditions(sim_time, scenario)

            # 2. 운영자 설정값 (자-테스트 흉내)
            needed = _required_dose(ntu) + algae_extra  # 조류 보정 포함
            setpoint = policy.setpoint_at(sim_time, needed, ntu)

            # 3. PLC2 역할: 유량 × 주입률 ÷ 1000
            chemical_pump_command = round(PRODUCTION_FLOW * setpoint / 1000, 4)
            chemical_pump_command = min(max(chemical_pump_command, 0.0), CHEMICAL_PUMP_MAX)

            # 4. 약품·pH (SIS 없음: 항상 열림으로 가정)
            actual_dose = chemical_pump_command * 1000 / PRODUCTION_FLOW
            ph = _dosed_ph(actual_dose)

            # 5. 5시간 지연
            diff_queue.append(actual_dose - needed)
            settled = _settled_turbidity(diff_queue.popleft())

            # 6. 수위 (PLC1 역할)
            intake_flow = 2400.0 if intake_pump == 2 else 1600.0
            level += (intake_flow - PRODUCTION_FLOW) / TANK_AREA * (PROCESS_STEP_SECONDS / 3600)
            level = min(max(level, 0.0), TANK_HEIGHT)
            if level <= 3.3:
                intake_pump = 2
            elif level >= 3.7:
                intake_pump = 1

            # 7. 상태 표 갱신 (원수 탁도 외부 주입)
            _put(conn, 'AIT101', 1, ntu)

            # 8. CSV 기록
            writer.writerow([
                sim_time, round(ntu, 4), round(algae_extra, 4), water_type,
                round(setpoint, 4), round(actual_dose, 4), round(needed, 4),
                round(settled, 4), round(ph, 4), round(level, 4),
            ])

            # 진행 표시 (10%마다)
            if (step_number + 1) % (total_steps // 10) == 0:
                pct = (step_number + 1) / total_steps * 100
                print(f"  {pct:5.1f}% — sim_time={sim_time/3600:.1f}h, "
                      f"ntu={ntu:.1f}, type={water_type}, setpoint={setpoint:.2f} mg/L")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='정수장 학습 데이터 생성')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS, help='시뮬레이션 일수 (기본: 30)')
    parser.add_argument('--out', type=str, default=DEFAULT_OUT, help='출력 CSV 경로')
    args = parser.parse_args()
    build(days=args.days, out_path=args.out)
