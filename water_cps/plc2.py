"""PLC2: 약품 주입량 = 생산 유량 × 주입률 설정값 (설계 문서 5-2절). 받은 설정값을 검사하지 않는다."""

import time

from minicps.devices import PLC

from config import (
    ADDRESS, DEVICE_SLEEP_SECONDS, DOSE_SETPOINT, FIT102, INITIAL_CHEMICAL_PUMP_COMMAND,
    INITIAL_DOSE_SETPOINT, P201, PLC2_TAGS, PRODUCTION_FLOW, STATE, server_protocol, to_number,
    write_initial_values,
)

OWN_ADDRESS = ADDRESS['plc2']
PLC1_ADDRESS = ADDRESS['plc1']


class Plc2(PLC):

    def pre_loop(self):
        self.dose_setpoint = INITIAL_DOSE_SETPOINT
        self.production_flow = PRODUCTION_FLOW
        write_initial_values(self, OWN_ADDRESS, [P201, DOSE_SETPOINT],
                             [INITIAL_CHEMICAL_PUMP_COMMAND, INITIAL_DOSE_SETPOINT], DOSE_SETPOINT)

    def main_loop(self):
        while True:
            cycle_start = time.time()

            self.dose_setpoint = to_number(self.receive(DOSE_SETPOINT, OWN_ADDRESS), self.dose_setpoint)
            self.production_flow = to_number(self.receive(FIT102, PLC1_ADDRESS), self.production_flow)

            chemical_pump_command = round(self.production_flow * self.dose_setpoint / 1000, 4)
            self.set(P201, chemical_pump_command)
            self.send(P201, chemical_pump_command, OWN_ADDRESS)

            time.sleep(DEVICE_SLEEP_SECONDS)
            print('cycle_seconds=%.2f' % (time.time() - cycle_start))


if __name__ == '__main__':
    Plc2(name='plc2', state=STATE, protocol=server_protocol('plc2', PLC2_TAGS))
