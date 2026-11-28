"""게이트웨이 판정 시험 (보안 조치 3단계): 가짜 PLC로 gateway.py 의 8가지 검사·폴백·복귀·passthrough 를 본다.
minicps·mininet 없이 돈다. 관리자 권한 필요 없음, 1초.

실행: cd ~/water_cps && venv/bin/python gateway.py approve && venv/bin/python tests/test_gateway_offline.py
임시 폴더에서 돌아 logs/ 를 건드리지 않는다.
"""
import json
import os
import shutil
import sys
import tempfile

WATER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/ 의 위 폴더
HERE = tempfile.mkdtemp(prefix='gateway_test_')
os.chdir(HERE)
shutil.rmtree('logs', ignore_errors=True)
os.makedirs('logs')
os.makedirs('dataset', exist_ok=True)
shutil.copy(os.path.join(WATER, 'dataset/approved_models.json'), 'dataset/approved_models.json')
sys.path.insert(0, WATER)
sys.argv = ['gateway.py']
import gateway  # noqa: E402

HASH = json.load(open('dataset/approved_models.json'))['approved'][0]['model_hash']
FAILS = []


class Fake(gateway.Gateway):
    def __init__(self, passthrough=False):
        self.plc = {'turb': 6.0, 'sim': 1000.0, 'dose': 17.0, 'alarm': 0}
        self.writes = []
        sys.argv = ['gateway.py'] + (['--passthrough'] if passthrough else [])

    def receive_multiple(self, tags, address):
        return [str(self.plc['turb']), str(self.plc['sim'])]

    def receive(self, tag, address):
        return str(self.plc['dose'] if tag[0] == 'DOSE_SETPOINT' else self.plc['alarm'])

    def send(self, tag, value, address):
        self.writes.append(value)
        self.plc['dose'] = value

    def start(self):
        # main_loop 의 준비 부분만 (무한 루프 전까지)
        src = gateway.Gateway.main_loop
        try:
            gateway.time.sleep, saved = (lambda s: (_ for _ in ()).throw(StopIteration)), gateway.time.sleep
            orig_cycle = self.cycle
            self.cycle = lambda: None
            src(self)
        except StopIteration:
            pass
        finally:
            gateway.time.sleep = saved
            del self.cycle

    def step(self, seconds=60):
        self.plc['sim'] += seconds
        self.cycle()


SEQ = [100]


def packet(gw, setpoint, **over):
    SEQ[0] += 1
    p = {'process': 'chemical', 'setpoint': setpoint, 'unit': 'mg/L', 'created_at': 'x', 'seq': SEQ[0],
         'model_version': 'v', 'model_hash': HASH,
         'inputs': {'turbidity_ntu': gw.plc['turb'], 'turbidity_trend': 0.0, 'sim_time': gw.plc['sim']},
         'water_type': 'normal', 'confidence': 1.0, 'parts': {}, 'flags': []}
    for key, value in over.items():
        if key.startswith('in_'):
            p['inputs'][key[3:]] = value
        else:
            p[key] = value
    return p


def push(obj):
    with open('logs/ai_predictions.jsonl', 'a') as f:
        f.write((obj if isinstance(obj, str) else json.dumps(obj)) + '\n')


def last_log():
    return json.loads(open('logs/gateway.log').read().strip().splitlines()[-1])


def expect(name, got, want):
    ok = got == want
    print('[%s] %s: got %s' % ('PASS' if ok else 'FAIL', name, got))
    if not ok:
        FAILS.append(name)


def judged(gw, obj, keep_rejects=False):
    push(obj)
    gw.step(5)
    entries = [json.loads(l) for l in open('logs/gateway.log').read().splitlines()]
    entry = [e for e in entries if e['result'] in ('accept', 'reject', 'ignored')][-1]
    if not keep_rejects:
        gw.rejects = 0   # 검사 하나씩 볼 때는 연속 거부 폴백이 끼지 않게
    return entry['result'], entry['stage']


# ---------------------------------------------------------------- 검사 모드
open('logs/ai_predictions.jsonl', 'w').write(json.dumps({'old': 'line from previous run'}) + '\n')
gw = Fake()
gw.start()
expect('start log', last_log()['result'], 'start')
gw.step(60)
base = gw.baseline_now()
print('baseline at 6 NTU = %.2f' % base)

expect('old file lines skipped', os.path.exists('logs/gateway.log') and
       all(json.loads(l)['result'] == 'start' for l in open('logs/gateway.log')), True)
expect('normal 19.0 passes', judged(gw, packet(gw, 19.0)), ('accept', None))
expect('PLC2 written 19.0', gw.plc['dose'], 19.0)
expect('1 format: not JSON', judged(gw, '{broken'), ('reject', '1_format'))
expect('1 format: unit', judged(gw, packet(gw, 19.0, unit='g/L')), ('reject', '1_format'))
expect('1 format: setpoint string', judged(gw, packet(gw, 'abc')), ('reject', '1_format'))
gw.rejects = 0
expect('2 model hash', judged(gw, packet(gw, 19.0, model_hash='0' * 64)), ('reject', '2_model'))
expect('3 time: 16 min old', judged(gw, packet(gw, 19.0, in_sim_time=gw.plc['sim'] - 965)), ('reject', '3_time'))
gw.rejects = 0
expect('3 time: future', judged(gw, packet(gw, 19.0, in_sim_time=gw.plc['sim'] + 600)), ('reject', '3_time'))
replay = packet(gw, 19.5)
expect('still ai mode', gw.mode, 'ai')
expect('fresh 19.5 passes', judged(gw, replay), ('accept', None))
replay['inputs']['sim_time'] = gw.plc['sim']
expect('3 seq: replay', judged(gw, replay), ('reject', '3_seq'))
gw.rejects = 0
expect('4 input: turbidity 30 vs 6', judged(gw, packet(gw, 19.0, in_turbidity_ntu=30.0)), ('reject', '4_input'))
expect('5 range: 80', judged(gw, packet(gw, 80.0)), ('reject', '5_range'))
gw.rejects = 0
expect('6 step: +6', judged(gw, packet(gw, gw.plc['dose'] + 6)), ('reject', '6_step'))
gw.plc['dose'] = round(base, 2)   # ⑥ 이 먼저 걸리지 않게
expect('7 baseline: base-4', judged(gw, packet(gw, round(base - 4, 2))), ('reject', '7_baseline'))
gw.rejects = 0

# ④ 호우: 탁도가 빠르게 오를 때, 패킷 시각의 PLC1 값과 비교하므로 정상 패킷이 통과해야 한다
gw.plc['turb'] = 100.0
gw.step(30)
gw.plc['dose'] = 27.0   # 100 NTU 기준값(27.85) 근처. ⑥⑦ 이 먼저 걸리지 않게
storm = packet(gw, 28.0)
gw.plc['turb'] = 115.0   # 게이트웨이가 읽을 때 이미 15 NTU 올라감 (지금 값과 비교하면 허용 ±11.5 초과)
expect('4 input: storm uses value at packet time', judged(gw, storm), ('accept', None))
gw.plc['turb'] = 6.0
gw.step(30)

# ⑧ 1시간 전 적용값: 기록을 직접 넣어 본다
gw.applied_history.appendleft((gw.plc['sim'] - 3700, 9.0))
target = gw.plc['dose'] + 4   # ⑥ 은 통과, 1시간 전 9.0 과는 10 넘게 차이
gw.plc['dose'] = 19.5
expect('8 hourly: >10 from 1h ago', judged(gw, packet(gw, 23.5)), ('reject', '8_hourly'))
gw.applied_history.clear()

# 폴백: 연속 3번 거부
gw.rejects = 0
for i in range(3):
    judged(gw, packet(gw, 80.0), keep_rejects=True)
entries = [json.loads(l) for l in open('logs/gateway.log')]
fb = [e for e in entries if e['result'] == 'fallback']
expect('fallback after 3 rejects', fb[-1]['stage'] if fb else None, 'consecutive_rejects')
expect('mode file fallback', open('logs/gateway_mode.txt').read(), 'fallback')
expect('PLC2 = baseline in fallback', round(gw.plc['dose'], 2), round(gw.baseline_now(), 2))
expect('passing packet ignored in fallback', judged(gw, packet(gw, round(gw.plc['dose'] + 1, 2))), ('ignored', None))

# 재시작해도 폴백 유지
gw2 = Fake()
gw2.plc = dict(gw.plc)
gw2.start()
expect('restart keeps fallback', gw2.mode, 'fallback')

# 복귀
os.system('%s %s/gateway.py resume > /dev/null' % (sys.executable, WATER))
gw.step(5)
expect('resume logged', last_log()['result'], 'resume')
expect('mode ai after resume', gw.mode, 'ai')
expect('accept after resume', judged(gw, packet(gw, round(gw.plc['dose'] + 1, 2))), ('accept', None))

# 끊김: 공정 15분 넘게 통과 없음
gw.step(901)
entries = [json.loads(l) for l in open('logs/gateway.log')]
expect('fallback on stale', [e for e in entries if e['result'] == 'fallback'][-1]['stage'], 'stale')
os.system('%s %s/gateway.py resume > /dev/null' % (sys.executable, WATER))
gw.step(5)

# 경보
gw.plc['alarm'] = 1
gw.step(5)
entries = [json.loads(l) for l in open('logs/gateway.log')]
expect('fallback on alarm', [e for e in entries if e['result'] == 'fallback'][-1]['stage'], 'turbidity_alarm')
gw.plc['alarm'] = 0

# 덜 쓰인 줄
gw3 = Fake()
gw3.plc['dose'] = 17.0
os.remove('logs/gateway_mode.txt')
gw3.start()
gw3.step(60)
with open('logs/ai_predictions.jsonl', 'a') as f:
    text = json.dumps(packet(gw3, 18.0))
    f.write(text[:20])
gw3.step(1)
expect('partial line not consumed', gw3.plc['dose'], 17.0)
with open('logs/ai_predictions.jsonl', 'a') as f:
    f.write(text[20:] + '\n')
gw3.step(1)
expect('completed line accepted', gw3.plc['dose'], 18.0)

# AI 재시작으로 파일이 짧아짐
open('logs/ai_predictions.jsonl', 'w').close()
push(packet(gw3, 19.0))
gw3.step(1)
expect('file truncated then new packet', gw3.plc['dose'], 19.0)

# PLC2 를 못 읽음 (nan)
gw4 = Fake()
gw4.receive = lambda tag, address: 'nan' if tag[0] == 'DOSE_SETPOINT' else '0'
os.remove('logs/gateway_mode.txt')
gw4.start()
gw4.step(60)
expect('6 step: PLC2 unreadable', judged(gw4, packet(gw4, 18.0)), ('reject', '6_step'))

# log 형식
keys = {'sim_time', 'seq', 'setpoint', 'result', 'stage', 'reason', 'mode'}
expect('every log line has the 7 keys',
       all(set(json.loads(l)) == keys for l in open('logs/gateway.log')), True)

# 경쟁 조건 (verify_security N on): 게이트웨이가 PLC1 을 읽은 직후 PLC1 시각이 오르고 AI가 새 시각으로 패킷을 쓴다.
# 패킷을 PLC보다 먼저 읽으므로 이 패킷은 다음 주기에, 그때 읽은 더 새 시각과 비교된다 → ③ 에서 거부되면 안 된다
if os.path.exists('logs/gateway_mode.txt'):
    os.remove('logs/gateway_mode.txt')
gw6 = Fake()
gw6.start()
gw6.step(60)
plain_read = gw6.receive_multiple


def racing_read(tags, address):
    result = plain_read(tags, address)   # 게이트웨이가 읽은 값은 옛 시각
    gw6.plc['sim'] += 60                 # 그 직후 PLC1 이 시각을 올리고
    push(packet(gw6, 17.5))              # AI 가 그 새 시각으로 패킷을 쓴다
    gw6.receive_multiple = plain_read
    return result


gw6.receive_multiple = racing_read
mark = len(open('logs/gateway.log').read().splitlines())
gw6.cycle()
gw6.cycle()
new = [json.loads(l) for l in open('logs/gateway.log').read().splitlines()[mark:]]
expect('race: packet newer than gateway PLC1 read is not rejected as future',
       [(e['result'], e['stage']) for e in new], [('accept', None)])

# ---------------------------------------------------------------- passthrough
gw5 = Fake(passthrough=True)
gw5.start()
expect('passthrough mode', gw5.mode, 'passthrough')
gw5.step(60)
push(packet(gw5, 80.0, model_hash='bad'))
gw5.step(5)
expect('passthrough writes 80 unchecked', gw5.plc['dose'], 80.0)
expect('passthrough log', (last_log()['result'], last_log()['mode']), ('accept', 'passthrough'))
expect('passthrough does not touch mode file', os.path.exists('logs/gateway_mode.txt') and
       open('logs/gateway_mode.txt').read() != 'passthrough', True)

print('\nFAILS:', FAILS)
shutil.rmtree(HERE, ignore_errors=True)
print('RESULT=%s' % ('PASS' if not FAILS else 'FAIL'))
