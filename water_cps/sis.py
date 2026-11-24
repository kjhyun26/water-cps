"""SIS: 주입 후 pH가 5.8~8.5를 2주기 연속 벗어나면 약품 차단밸브를 닫는다 (설계 문서 5-4절).

다른 PLC의 서버는 읽지 않는다. 통신으로 받는 것은 HMI가 써 준 복귀 요청뿐이다.
"""

import time

from minicps.devices import PLC

from config import (
    ADDRESS, AIT202, DEVICE_SLEEP_SECONDS, INITIAL_DOSED_PH, INITIAL_SHUTOFF_VALVE, PH_HIGH, PH_LOW,
    SIS_RESET_REQUEST, SIS_TAGS, SIS_TRIP_COUNT, STATE, XV201, server_protocol, to_number,
    write_initial_values,
)

OWN_ADDRESS = ADDRESS['sis']


class Sis(PLC):

    def pre_loop(self):
        self.ph = INITIAL_DOSED_PH
        self.valve = INITIAL_SHUTOFF_VALVE
        self.out_of_range_count = 0
        write_initial_values(self, OWN_ADDRESS, [AIT202, XV201, SIS_RESET_REQUEST],
                             [INITIAL_DOSED_PH, INITIAL_SHUTOFF_VALVE, 0], XV201)

    def main_loop(self):
        while True:
            cycle_start = time.time()
            self.cycle()
            time.sleep(DEVICE_SLEEP_SECONDS)
            print('cycle_seconds=%.2f' % (time.time() - cycle_start))

    def cycle(self):
        self.ph = to_number(self.get(AIT202), self.ph)
        in_range = PH_LOW <= self.ph <= PH_HIGH
        self.out_of_range_count = 0 if in_range else self.out_of_range_count + 1
        if self.out_of_range_count >= SIS_TRIP_COUNT and self.valve == 1:
            self.valve = 0
            print('SIS trip: pH %.3f' % self.ph)

        request = int(to_number(self.receive(SIS_RESET_REQUEST, OWN_ADDRESS), 0))
        if request == 1:
            if self.valve == 0 and in_range:
                self.valve = 1
                self.out_of_range_count = 0
                print('SIS reset accepted: pH %.3f' % self.ph)
            else:
                print('SIS reset ignored: valve %d, pH %.3f' % (self.valve, self.ph))

        # 바뀔 때만이 아니라 매 주기 쓴다: 쓰기 한 번이 실패해도 다음 주기에 맞춰진다
        self.set(XV201, self.valve)
        if request == 1:
            self.send_multiple([AIT202, XV201, SIS_RESET_REQUEST],
                               [round(self.ph, 4), self.valve, 0], OWN_ADDRESS)
        else:
            self.send_multiple([AIT202, XV201], [round(self.ph, 4), self.valve], OWN_ADDRESS)


if __name__ == '__main__':
    Sis(name='sis', state=STATE, protocol=server_protocol('sis', SIS_TAGS))
