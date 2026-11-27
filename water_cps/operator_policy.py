"""운영자 정책: 자-테스트 기반 주입률 설정값을 흉내 낸다.

실제 운영자는 하루 한 번 자-테스트(소규모 비커 실험)로 필요 주입률을 측정하고,
약간 넉넉히(과주입) 넣는다. 그 사이에는 직전 설정값을 그대로 쓴다.
(도메인 문서 2-5절, 설계 문서 9-3절)

이 모듈은 minicps를 import하지 않는다. 공정 시뮬레이션과 학습 스크립트 모두에서 쓰기 때문.
"""

from config import INITIAL_DOSE_SETPOINT


class OperatorPolicy:
    """자-테스트 기반 주입률 운영자 정책.

    Args:
        overdose: 과주입량 (mg/L). 필요량 위에 더하는 안전 여유.
        jar_test_hours: 하루 중 자-테스트를 수행하는 시각 (정수 시간, 복수 허용).
        emergency_threshold_ntu: 이 탁도를 넘으면 즉시 비상 주입률로 올린다.
        emergency_overdose: 비상 시 과주입량 (mg/L).
    """

    def __init__(
        self,
        overdose: float = 2.0,
        jar_test_hours: tuple = (9,),
        emergency_threshold_ntu: float = 100.0,
        emergency_overdose: float = 5.0,
    ):
        self.overdose = overdose
        self.jar_test_hours = set(jar_test_hours)
        self.emergency_threshold_ntu = emergency_threshold_ntu
        self.emergency_overdose = emergency_overdose
        self.setpoint = INITIAL_DOSE_SETPOINT
        self._last_jar_test = None  # (날짜, 시각). 같은 날 같은 시각에 두 번 갱신 방지

    def setpoint_at(
        self,
        sim_time_seconds: float,
        true_required_dose: float,
        raw_turbidity_ntu: float,
    ) -> float:
        """현재 공정 시각에 맞는 주입률 설정값(mg/L)을 반환한다.

        Args:
            sim_time_seconds: 공정 시각 (초).
            true_required_dose: 그 시각의 실제 필요 주입률 (mg/L).
                자-테스트로 측정한 값을 흉내 낸다. 센서로는 알 수 없으므로
                학습 데이터 생성 시에만 사용한다.
            raw_turbidity_ntu: 원수 탁도 (NTU). 비상 판정에 사용.

        Returns:
            주입률 설정값 (mg/L).
        """
        hour_of_day = int(sim_time_seconds // 3600) % 24
        day = int(sim_time_seconds // 86400)

        # 비상: 탁도가 임계값을 넘으면 즉시 올린다
        if raw_turbidity_ntu >= self.emergency_threshold_ntu:
            self.setpoint = true_required_dose + self.emergency_overdose
            return round(self.setpoint, 4)

        # 정시 자-테스트 (날마다 정한 시각에 한 번씩)
        if hour_of_day in self.jar_test_hours and (day, hour_of_day) != self._last_jar_test:
            self.setpoint = true_required_dose + self.overdose
            self._last_jar_test = (day, hour_of_day)

        return round(self.setpoint, 4)

    def reset(self):
        """시나리오 재실행 시 상태 초기화."""
        self.setpoint = INITIAL_DOSE_SETPOINT
        self._last_jar_test = None


# ---------------------------------------------------------------------------
# 간단 확인
# ---------------------------------------------------------------------------
def _self_check():
    import math
    from config import REQUIRED_DOSE_BASE, REQUIRED_DOSE_SLOPE, MINIMUM_RAW_TURBIDITY

    def required_dose(raw_turbidity):
        return REQUIRED_DOSE_BASE + REQUIRED_DOSE_SLOPE * math.log(
            max(raw_turbidity, MINIMUM_RAW_TURBIDITY)
        )

    policy = OperatorPolicy(overdose=2.0, jar_test_hours=(9,))

    # 자-테스트 시각: 9시 (9 * 3600 초)
    dose_9am = policy.setpoint_at(9 * 3600, required_dose(6.0), 6.0)
    expected = round(required_dose(6.0) + 2.0, 4)
    assert abs(dose_9am - expected) < 0.01, f"9am setpoint wrong: {dose_9am} vs {expected}"

    # 같은 시간 두 번 호출 → 값 그대로
    dose_9am_again = policy.setpoint_at(9 * 3600 + 30, required_dose(6.0), 6.0)
    assert dose_9am_again == dose_9am, "should not update twice in same hour"

    # 비상 (탁도 300 NTU)
    dose_emergency = policy.setpoint_at(10 * 3600, required_dose(300.0), 300.0)
    assert dose_emergency > required_dose(300.0), "emergency dose should exceed required"

    # 다음 날 9시에도 자-테스트를 한다
    dose_next_day = policy.setpoint_at(86400 + 9 * 3600, required_dose(6.0) + 5.0, 6.0)
    assert abs(dose_next_day - (required_dose(6.0) + 7.0)) < 0.01, "should jar-test again next day"

    print("operator_policy.py OK")


if __name__ == '__main__':
    _self_check()
