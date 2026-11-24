"""PLC3: 침전수 탁도가 1.0 NTU를 공정 30분 넘게 넘으면 경보를 낸다 (설계 문서 5-3절)."""

import time

from minicps.devices import PLC

from config import (
    ADDRESS, AIT301, DEVICE_SLEEP_SECONDS, INITIAL_SETTLED_TURBIDITY, PLC3_TAGS, SIM_TIME, STATE,
    TURBIDITY_ALARM,
    TURBIDITY_ALARM_MINUTES, TURBIDITY_LIMIT, TURBIDITY_OVER_MINUTES, server_protocol, to_number,
    write_initial_values,
)

OWN_ADDRESS = ADDRESS['plc3']
SERVER_TAGS = [AIT301, TURBIDITY_OVER_MINUTES, TURBIDITY_ALARM]


class Plc3(PLC):

    def pre_loop(self):
        self.settled_turbidity = INITIAL_SETTLED_TURBIDITY
        self.previous_sim_time = to_number(self.get(SIM_TIME), 0.0)
        self.over_minutes = 0.0
        write_initial_values(self, OWN_ADDRESS, SERVER_TAGS, [INITIAL_SETTLED_TURBIDITY, 0.0, 0], AIT301)

    def main_loop(self):
        while True:
            cycle_start = time.time()
            self.cycle()
            time.sleep(DEVICE_SLEEP_SECONDS)
            print('cycle_seconds=%.2f' % (time.time() - cycle_start))

    def cycle(self):
        self.settled_turbidity = to_number(self.get(AIT301), self.settled_turbidity)
        now = to_number(self.get(SIM_TIME), self.previous_sim_time)
        elapsed_minutes = (now - self.previous_sim_time) / 60
        self.previous_sim_time = now

        if self.settled_turbidity > TURBIDITY_LIMIT:
            self.over_minutes += elapsed_minutes
        else:
            self.over_minutes = 0.0
        alarm = 1 if self.over_minutes >= TURBIDITY_ALARM_MINUTES else 0

        self.send_multiple(SERVER_TAGS, [
            round(self.settled_turbidity, 4), round(self.over_minutes, 4), alarm,
        ], OWN_ADDRESS)


if __name__ == '__main__':
    Plc3(name='plc3', state=STATE, protocol=server_protocol('plc3', PLC3_TAGS))
