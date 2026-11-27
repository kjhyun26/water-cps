"""원수 시나리오: 공정 시각(초)을 받아 원수 탁도(NTU)와 원수 유형을 돌려준다.

이 계층은 원수를 정하지 않는다(설계 문서 1-3절). 그래서 water_cps/ 안에 두되
물리·제어 계층과 분리해 바깥 값으로 주입한다.

지원 유형: 평상시(normal), 고탁도(high_turbidity), 호우(storm), 조류(algae)
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple


# ---------------------------------------------------------------------------
# 유형 레이블 (모델 분류 단계에서 사용)
# ---------------------------------------------------------------------------
TYPE_NORMAL = 'normal'
TYPE_HIGH_TURBIDITY = 'high_turbidity'
TYPE_STORM = 'storm'
TYPE_ALGAE = 'algae'


# ---------------------------------------------------------------------------
# 이벤트 정의
# ---------------------------------------------------------------------------
@dataclass
class StormEvent:
    """호우 이벤트. 탁도가 급격히 오르고 천천히 내려온다."""
    start_seconds: float      # 호우 시작 공정 시각 (초)
    peak_ntu: float           # 정점 탁도 (NTU), 권장 범위: 100~500
    rise_hours: float = 3.0   # 오르는 시간 (시간)
    fall_hours: float = 12.0  # 내려오는 시간 (시간)


@dataclass
class HighTurbidityEvent:
    """호우 없는 고탁도 기간 (상류 공사·준설, 저수지 바닥 교란 등).
    호우보다 천천히 올라 오래 머문다. 탁도 변화율로 호우와 구분된다."""
    start_seconds: float       # 시작 공정 시각 (초)
    peak_ntu: float            # 머무는 탁도 (NTU)
    rise_hours: float = 12.0   # 오르는 시간
    hold_hours: float = 36.0   # 머무는 시간
    fall_hours: float = 12.0   # 내려오는 시간


@dataclass
class AlgaeEvent:
    """조류 이벤트. 탁도는 그대로지만 필요 주입률이 추가로 오른다.
    실제 모델에서는 조류 영향을 required_dose()에 외부 보정값으로 더해야 한다.
    여기서는 탁도 외에 algae_factor를 함께 반환한다.
    """
    start_seconds: float      # 시작 공정 시각
    end_seconds: float        # 종료 공정 시각
    extra_dose_mg_per_l: float = 5.0  # 필요 주입률 추가량 (mg/L)


@dataclass
class Scenario:
    """30일치 원수 시나리오 한 세트."""
    base_turbidity: float = 6.0           # 평상시 기준 탁도 (NTU)
    daily_amplitude: float = 1.0          # 하루 주기 흔들림 폭 (NTU)
    storms: List[StormEvent] = field(default_factory=list)
    high_turbidity_events: List[HighTurbidityEvent] = field(default_factory=list)
    algae_events: List[AlgaeEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 기본 시나리오 (30일, 호우 3번, 고탁도 1번, 조류 1번)
# ---------------------------------------------------------------------------
def default_scenario() -> Scenario:
    """설계 문서 9-3절 참고. 도메인 문서 4-6절의 수질 패턴을 반영한다."""
    return Scenario(
        base_turbidity=6.0,
        daily_amplitude=1.0,
        storms=[
            StormEvent(start_seconds=5 * 86400, peak_ntu=300.0),   # 5일째 호우 (고탁도)
            StormEvent(start_seconds=14 * 86400, peak_ntu=150.0),  # 14일째 중탁도 호우
            StormEvent(start_seconds=23 * 86400, peak_ntu=400.0),  # 23일째 대형 호우
        ],
        high_turbidity_events=[
            # 9일째 고탁도: 12시간에 걸쳐 80 NTU까지 올라 36시간 머문다.
            # 운영자 비상 기준(100 NTU)보다 낮아 운영자는 다음 자-테스트까지 늦게 따라간다
            HighTurbidityEvent(start_seconds=9 * 86400, peak_ntu=80.0),
        ],
        algae_events=[
            AlgaeEvent(
                start_seconds=18 * 86400,
                end_seconds=21 * 86400,
                extra_dose_mg_per_l=5.0,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 핵심 함수
# ---------------------------------------------------------------------------
def raw_conditions(sim_time_seconds: float, scenario: Scenario) -> Tuple[float, float, str]:
    """공정 시각(초)을 받아 (원수 탁도 NTU, 조류 추가 주입률 mg/L, 유형 레이블)을 반환한다.

    탁도 계산 순서:
    1. 기준 탁도 + 하루 주기 흔들림
    2. 호우 기여분 (여러 호우가 겹치면 최댓값)
    3. 고탁도 기여분
    4. 조류 추가 주입률, 유형 판정

    Args:
        sim_time_seconds: 공정 시각 (초). config.PROCESS_STEP_SECONDS 단위로 증가.
        scenario: 사용할 시나리오.

    Returns:
        (탁도, 조류 추가 주입률, 유형 레이블)
    """
    hours = sim_time_seconds / 3600.0

    # 1. 기준 + 하루 주기
    value = scenario.base_turbidity + scenario.daily_amplitude * math.sin(
        hours / 24.0 * 2.0 * math.pi
    )

    # 2. 호우 기여분
    storm_contribution = 0.0
    for storm in scenario.storms:
        since_hours = (sim_time_seconds - storm.start_seconds) / 3600.0
        if 0 <= since_hours < storm.rise_hours:
            # 오름: 로그 눈금 직선 (지수 보간)
            t = since_hours / storm.rise_hours
            contribution = scenario.base_turbidity * (storm.peak_ntu / scenario.base_turbidity) ** t
            storm_contribution = max(storm_contribution, contribution)
        elif storm.rise_hours <= since_hours < storm.rise_hours + storm.fall_hours:
            # 내림: 로그 눈금 직선
            t = (since_hours - storm.rise_hours) / storm.fall_hours
            contribution = storm.peak_ntu * (scenario.base_turbidity / storm.peak_ntu) ** t
            storm_contribution = max(storm_contribution, contribution)

    # 3. 고탁도 기여분
    high_contribution = 0.0
    base = scenario.base_turbidity
    for event in scenario.high_turbidity_events:
        since_hours = (sim_time_seconds - event.start_seconds) / 3600.0
        hold_end = event.rise_hours + event.hold_hours
        if 0 <= since_hours < event.rise_hours:
            contribution = base * (event.peak_ntu / base) ** (since_hours / event.rise_hours)
        elif event.rise_hours <= since_hours < hold_end:
            contribution = event.peak_ntu
        elif hold_end <= since_hours < hold_end + event.fall_hours:
            contribution = event.peak_ntu * (base / event.peak_ntu) ** ((since_hours - hold_end) / event.fall_hours)
        else:
            continue
        high_contribution = max(high_contribution, contribution)

    # 사건이 실제로 탁도를 정할 때만 그 유형으로 본다. 사건 꼬리에서 탁도가 평상시 수준으로
    # 돌아온 뒤까지 호우라고 부르면, 센서로 배울 수 없는 레이블이 된다.
    storm_dominant = storm_contribution > value and storm_contribution >= high_contribution
    high_dominant = high_contribution > value and not storm_dominant
    value = max(value, storm_contribution, high_contribution)

    # 4. 조류 추가 주입률
    algae_extra = 0.0
    for event in scenario.algae_events:
        if event.start_seconds <= sim_time_seconds < event.end_seconds:
            algae_extra = max(algae_extra, event.extra_dose_mg_per_l)

    # 5. 유형 판정
    label = _classify(storm_dominant, high_dominant, algae_extra)

    return round(value, 4), round(algae_extra, 4), label


def _classify(storm_dominant: bool, high_dominant: bool, algae_extra: float) -> str:
    """이벤트 정보로 유형 레이블을 결정한다.

    우선순위: 호우 > 고탁도 > 조류 > 평상시
    호우: 호우 기여분이 평상시 탁도보다 커서 탁도를 정하고 있음
    고탁도: 고탁도 사건 기여분이 탁도를 정하고 있음 (호우 없이)
    조류: algae_extra > 0
    """
    if storm_dominant:
        return TYPE_STORM
    if high_dominant:
        return TYPE_HIGH_TURBIDITY
    if algae_extra > 0:
        return TYPE_ALGAE
    return TYPE_NORMAL


# ---------------------------------------------------------------------------
# 간단 확인 (import할 때 돈다)
# ---------------------------------------------------------------------------
def _self_check():
    s = default_scenario()

    # 평상시: 0초 → 기준 탁도 근처, normal
    ntu, extra, label = raw_conditions(0, s)
    assert label == TYPE_NORMAL, f"expected normal, got {label}"
    assert 4.0 < ntu < 8.0, f"unexpected normal turbidity: {ntu}"
    assert extra == 0.0

    # 호우 정점: 5일 + 3시간 (오름 끝, 정점 300 NTU)
    ntu_storm, _, label_storm = raw_conditions(5 * 86400 + 3 * 3600, s)
    assert label_storm == TYPE_STORM, f"expected storm, got {label_storm}"
    assert ntu_storm > 100.0, f"storm turbidity too low: {ntu_storm}"

    # 호우 직후 → 내려오는 중, still storm
    ntu_after, _, label_after = raw_conditions(5 * 86400 + 4 * 3600, s)
    assert label_after == TYPE_STORM

    # 조류 기간
    ntu_algae, extra_algae, label_algae = raw_conditions(19 * 86400, s)
    assert label_algae == TYPE_ALGAE, f"expected algae, got {label_algae}"
    assert extra_algae == 5.0

    # 고탁도 머무는 기간: 9일 + 24시간
    ntu_high, _, label_high = raw_conditions(9 * 86400 + 24 * 3600, s)
    assert label_high == TYPE_HIGH_TURBIDITY, f"expected high_turbidity, got {label_high}"
    assert abs(ntu_high - 80.0) < 0.01


_self_check()
