"""PLC1: 착수정 수위를 3.3~3.7 m 사이로 유지한다 (설계 문서 5-1절)."""

import time

from minicps.devices import PLC

from config import (
    ADDRESS, AIT101, DEVICE_SLEEP_SECONDS, FIT102, INITIAL_INTAKE_PUMP, INITIAL_LEVEL,
    INITIAL_RAW_TURBIDITY, LEVEL_HIGH, LEVEL_LOW, LIT101, P101, PLC1_TAGS, PRODUCTION_FLOW, SIM_TIME,
    SIM_TIME_PLC1, STATE, server_protocol, to_number, write_initial_values,
)

OWN_ADDRESS = ADDRESS['plc1']
SERVER_TAGS = [LIT101, FIT102, AIT101, P101, SIM_TIME_PLC1]


class Plc1(PLC):

    def pre_loop(self):
        self.level = INITIAL_LEVEL
        self.production_flow = PRODUCTION_FLOW
        self.raw_turbidity = INITIAL_RAW_TURBIDITY
        self.sim_time = 0.0
        self.intake_pump = INITIAL_INTAKE_PUMP
        write_initial_values(self, OWN_ADDRESS, SERVER_TAGS,
                             [INITIAL_LEVEL, PRODUCTION_FLOW, INITIAL_RAW_TURBIDITY, INITIAL_INTAKE_PUMP, 0.0],
                             FIT102)

    def main_loop(self):
        while True:
            cycle_start = time.time()

            self.level = to_number(self.get(LIT101), self.level)
            self.production_flow = to_number(self.get(FIT102), self.production_flow)
            self.raw_turbidity = to_number(self.get(AIT101), self.raw_turbidity)
            self.sim_time = to_number(self.get(SIM_TIME), self.sim_time)

            if self.level <= LEVEL_LOW:
                self.intake_pump = 2
            elif self.level >= LEVEL_HIGH:
                self.intake_pump = 1
            self.set(P101, self.intake_pump)

            self.send_multiple(SERVER_TAGS, [
                round(self.level, 4), round(self.production_flow, 4), round(self.raw_turbidity, 4),
                self.intake_pump, round(self.sim_time, 4),
            ], OWN_ADDRESS)

            time.sleep(DEVICE_SLEEP_SECONDS)
            print('cycle_seconds=%.2f' % (time.time() - cycle_start))


if __name__ == '__main__':
    Plc1(name='plc1', state=STATE, protocol=server_protocol('plc1', PLC1_TAGS))
