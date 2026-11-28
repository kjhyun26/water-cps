# CLAUDE.md — 정수장 MiniCPS: 보안 조치

정수장 MiniCPS 시뮬레이션(`water_cps/`)에 붙인 보안 조치의 범위, 구성, 결정 사항, 시험을 정리한다.
기준 문서는 AI 파트의 「게이트웨이 구현 안내」 문서이고, `gateway.py`는 그 문서 8장의 뼈대에서 출발했다.
프로젝트 전체 설명은 `README.md`(사람용 요약, "보안 조치" 절)에 있다. 개인 설정은 각자 `CLAUDE.local.md`(gitignore)에 둔다.

## 공용 규칙

- 기존 `tests/verify_system.py`는 계속 14/14 통과해야 한다. 확인 4가 `dose 110`으로 SIS를 일으키므로
  `run.start_devices`의 기본값은 보안 끔이다.
- `run.py`는 팀 공용 파일이다. 고치기 전에 `git pull`.
- Mininet 을 띄우는 시험(`check_firewall.py`, `verify_system.py`, `verify_security.py`)과 `run.py`는 sudo 가 필요하다.

## 범위

- **약품(응집제) 주입 공정만** 다룬다. 보호 대상은 PLC2 `DOSE_SETPOINT`(mg/L)다. 소독·착수정은 없다.
- 위협 번호는 팀 보안 설계 문서를 따른다: F1(터무니없는 예측), H1(외부에서 PLC 쓰기), H2(입력 위조),
  H3(HMI 장악), H4(천천히 낮추기), H5(모델 바꿔치기).

## 호스트와 IP (`config.ADDRESS`, `run.HOST_NAMES`)

| 호스트 | IP | 띄우는 것 |
|---|---|---|
| plc1 | 192.168.1.10 | plc1.py (수위, 원수 탁도 AIT101, SIM_TIME) |
| plc2 | 192.168.1.20 | plc2.py (주입률 DOSE_SETPOINT → 약품 펌프 P201) |
| plc3 | 192.168.1.30 | plc3.py (침전수 탁도 경보) |
| sis | 192.168.1.40 | sis.py |
| hmi | 192.168.1.50 | hmi.py, **gateway.py** |
| ai | 192.168.1.60 | ai_agent.py (`DIRECT_WRITE = False`, 패킷만 쓰고 PLC2에는 쓰지 않음) |
| attacker | 192.168.1.70 | 아무것도 띄우지 않음 (H1 시험용) |

공정(`physical_process.py`)과 원수 공급기는 s1(루트 이름공간)에서 SQLite 상태 표로 돈다.

## USE_SECURITY (`run.py` 맨 위, `start_devices(..., use_security=)`)

한 플래그로 세 가지를 함께 켜고 끈다.

| | 끔 (False) | 켬 (True) |
|---|---|---|
| iptables | 없음 | `apply_firewall()`이 장치마다 건다 |
| 게이트웨이 | `gateway.py --passthrough`: 검사 없이 패킷 값을 그대로 PLC2에 씀 | `gateway.py`: 8가지 검사·폴백 |
| PLC2 | 받은 값을 그대로 씀 | `plc2.py --secure`: 범위 거부 + ramp |

- 게이트웨이는 `use_ai_agent or use_security`일 때 띄운다. `start_devices` 기본값(전부 False)은 예전 구성과 같고
  `verify_system.py`가 이것을 시험한다.
- 끔에서도 passthrough 게이트웨이를 두는 이유: AI가 `DIRECT_WRITE=False`라, 없으면 끔 쪽에서 AI 경로 공격이
  "아무 일 없음"으로 나와 켬/끔 비교가 안 된다.

## 방화벽 (`config.FIREWALL_ALLOWED`, `run.firewall_commands`)

각 장치 INPUT 체인: `lo` 허용 → `ESTABLISHED,RELATED` 허용 → 아래 출발지만 44818(tcp·udp) ACCEPT → 나머지 44818 DROP.

| 서버 | 44818 새 연결을 받는 출발지 |
|---|---|
| plc1 | plc2, hmi, ai |
| plc2 | hmi |
| plc3 | hmi |
| sis | hmi |

- 장치는 자기 IP로 자기 서버에 쓰는데, 이 연결은 `lo`를 거치므로 허용된다.
- 규칙 하나라도 실패하면 `RuntimeError`(보안을 켰는데 꺼진 상태를 막음). 걸린 규칙은 `logs/firewall_rules.txt`.
- iptables 가 필요하다: `sudo apt install -y iptables` (WSL 기본 설치에 없다).
- 켬에서는 ai 노드가 PLC2를 못 읽어 `ai_agent.py status`의 "PLC2 현재 설정값"이 nan 이다. 설계대로다.

## 게이트웨이 (`water_cps/gateway.py`)

- 입력: `logs/ai_predictions.jsonl`(AI 패킷), PLC1 AIT101·SIM_TIME, PLC2 DOSE_SETPOINT, PLC3 TURBIDITY_ALARM, 기준값(`baseline.py` + `logs/jar_test.json`).
- 검사: ① 형식 ② 승인 모델 ③ 공정 시각 0~900초·seq 증가 ④ AI 탁도 vs PLC1 ≤ max(5 NTU, 10%)
  ⑤ 8~45 ⑥ PLC2 현재값 ±5 ⑦ 기준값 −3.5 미만 거부 ⑧ 1시간 전 적용값 ±10.
- 폴백(자동): 연속 3번 거부 / 통과한 예측이 공정 15분 없음(stale) / PLC3 경보. 폴백 중에는 매 주기 기준값을 씀.
  복귀는 운영자만 `hmi venv/bin/python gateway.py resume`. 재시작해도 폴백 유지(`logs/gateway_mode.txt`).
- 기록 `logs/gateway.log`(JSON lines): `{sim_time, seq, setpoint, result, stage, reason, mode}`.
  - result 는 start, accept, reject, ignored(폴백 중 통과), fallback, baseline, resume 중 하나.
  - stage 는 1_format, 2_model, 3_time, 3_seq, 4_input, 5_range, 6_step, 7_baseline, 8_hourly 중 하나. 폴백이면 원인(consecutive_rejects, stale, turbidity_alarm)이 들어간다.
  - mode 는 ai, fallback, passthrough 중 하나.
  - 콘솔 출력은 `logs/gateway_console.log`로 따로 간다.
- 뼈대에서 바꾼 점:
  - PLC 값이 nan 이면 거부한다(뼈대는 ④⑥을 통과시켰다).
  - ④는 패킷 sim_time 에 가장 가까운 PLC1 기록과 비교한다(호우 때 오탐 방지).
  - 덜 쓰인 줄은 다음 주기에 읽고, 시작할 때는 파일 끝부터 읽는다.
  - 자-테스트 갱신량은 실험 시각의 탁도로 계산한다.
- `run.clean_up_processes()`가 실행마다 `ai_predictions.jsonl`, `gateway.log`, `gateway_mode.txt`를 지운다.

### 승인 목록 (`water_cps/dataset/approved_models.json`)

- PDF는 `model_info.json`을 쓰지만, 학습 스크립트가 덮어쓰는 파일이라 별도 파일로 뒀다.
- 형식: `{"approved": [{"model_version", "model_hash", "approved_at", "by"}]}`.
- 만들기: `cd water_cps && venv/bin/python gateway.py approve`. 모델 파일 sha256 이 `model_info.json`과 같을 때만 추가한다.
- 모델 해시가 학습할 때마다 달라 `.gitignore`에 넣었다. 학습한 PC마다 approve 를 돌려야 한다.
  없으면 켬에서 모든 패킷이 ②에서 거부된다.

## PLC2 보호 (`water_cps/plc2.py --secure`)

- 범위: DOSE_SETPOINT 가 5~50 mg/L 밖이면 거부하고, 마지막으로 받아들인 **목표값**을 태그에 다시 쓴다.
  로그는 `DOSE_REJECT sim_time=… value=… limits=5~50 restored=…`.
- ramp (팀 결정 2026-11-29): 실제 주입률(펌프 명령에 쓰는 값)은 **공정 시각 어느 60초 구간에서도 2 mg/L 이하**로
  목표값을 따라간다(평균 2/분이 아님).
  - 방법: 지난 60초 동안 적용한 값들(60초 전에 적용 중이던 값 포함)과 모두 2 이내가 되도록 자른다.
  - 결과: PLC2 주기가 공정 90초면 실제 속도는 2 mg/L / 90초다. 목표를 받기 전 시간은 앞당겨 쓰지 않는다.
  - 공정 시각은 상태 표 `SIM_TIME`으로 잰다. 태그는 목표값을 그대로 보여 준다. 로그는 `DOSE_RAMP start/step/done`.
  - 예전 방식(경과 시간 × 2/60)은 공정 90초 주기에서 한 번에 +3을 움직였고, 받은 주기에 곧바로 +2를 움직였다.
  - "60초 구간" 검사는 계단 기준이다. 값 v_i 는 다음 기록 직전까지 유효하므로 t_j − t_{i+1} < 60 이면 같은 구간으로 본다
    (`window_violations`, 시험 두 곳에 있다). 기록 시점끼리만 비교하면 큰 계단을 놓친다.
- 판단은 `Plc2.control(received)` 한 곳에 있다. 오프라인 시험도 이 메서드를 부른다.
- 켬에서는 `dose 110`으로 SIS 를 일으킬 수 없다(PLC2가 거부). pH 5.8 은 약 106 mg/L 이라 50 한계 안에서는 SIS 가 걸리지 않는다.

## 시험 (`water_cps/tests/`)

| 파일 | 권한 | 시간 | 내용 |
|---|---|---|---|
| `test_gateway_offline.py` | 없음 | 1초 | 가짜 PLC로 게이트웨이 검사·폴백·복귀·passthrough, PLC 읽기 중 경쟁 조건 (39개) |
| `test_plc2_offline.py` | 없음 | 1초 | PLC2 범위 거부·되돌려 쓰기·ramp, 불규칙 주기 60초 구간 성질 시험 (27개) |
| `check_firewall.py` | sudo | 1분 | 호스트 7대 → 서버 4대 접속 행렬, 방화벽 없이/건 뒤 |
| `verify_system.py` | sudo | 9분 | 기존 확인 1~6 (보안 끔). 14/14 유지해야 함 |
| `verify_security.py` | sudo | 약 12분 | 시나리오를 보안 끔/켬 두 번 돌려 비교 |

```bash
cd water_cps
venv/bin/python tests/test_gateway_offline.py
venv/bin/python tests/test_plc2_offline.py
# 아래는 sudo
sudo python3 tests/check_firewall.py 2>&1 | tee logs/check_firewall.txt
sudo python3 tests/verify_system.py 2>&1 | tee logs/verify_system_<태그>.txt
sudo python3 tests/verify_security.py 2>&1 | tee logs/verify_security_run.txt
sudo python3 tests/verify_security.py --mode on --only H1 H3      # 일부만
```

### verify_security.py 시나리오

- **판정 기준:** 끔에서는 공격이 효과를 내야 PASS 이고(비교가 성립하는지 확인), 켬에서는 막혀야 PASS 다.
- **판정 근거:** `process.csv`의 실제 주입률·pH·차단밸브, `gateway.log` 거부 단계·폴백 원인, `plc2.log`, iptables DROP 카운터.
- **시나리오 사이 되돌리기:** 탁도 6, `hmi.py dose 18.18`(6 NTU 기준값), SIS 복귀, (켬) resume, AI 재시작 뒤 accept 대기.
- **패킷 공격(F1·H4·H2·H5) 방식:** AI 노드 장악으로 본다. AI 프로세스를 멈추고, 같은 형식의 패킷을 파일에 쓴다.
  - 멈출 때 정상 패킷 하나(keepalive)를 넣는다. 시나리오 도중 stale 폴백이 끼지 않게 하려는 것이다.
  - **게이트웨이 로그에 그 seq 가 찍힐 때까지 기다린 뒤** 시나리오를 시작한다. 기다리지 않으면 keepalive 쓰기가
    시나리오의 첫 쓰기를 덮어쓰거나, 바뀐 탁도로 거부된다.
  - 주입 패킷의 `inputs.sim_time`은 상태 표 시각 −150초로 둔다. PLC1 SIM_TIME 이 상태 표보다 한 주기 늦어서, 그대로 두면 ③ "미래"로 거부된다.

| 이름 | 공격 | 끔 기대 | 켬 기대 |
|---|---|---|---|
| N | 정상 운전 공정 30분 | accept ≥3 | accept ≥3, 거부 0, 폴백 없음 |
| F1 | 패킷 setpoint 80 | 80 적용 | 40 미만, 5_range |
| H4 | 공정 5분마다 −0.3, 20번 | 기준값−3.5(14.68) 아래 | 최저 ≥14.68, 7_baseline |
| H2 | 실제 탁도 60(300초 대기), 패킷 탁도 6, 120초 간격 4번 | 모자란 값 유지 | 4_input, consecutive_rejects 폴백, 최종 ≥ 기준값(60)−3.5 |
| H5 | 승인 안 된 해시, 값 15.17 | 적용 | 2_model 만, 적용 안 됨 |
| H1 | attacker 에서 `hmi.py dose 45` | 45 적용 | 40 미만, PLC2 DROP 카운터 증가 |
| H3 | hmi 에서 dose 200 | PLC2 명령 400 kg/h, SIS 차단 (실제 주입률은 펌프 최대 250 kg/h = 125 mg/L 에서 잘림) | 명령 ≤100 kg/h, SIS 열림, DOSE_REJECT |
| R | hmi 에서 +10 | 한 번에 점프 ≥9.9 | 목표 도달, 어느 60초 구간도 2 초과 없음 (mark 직전 행부터) |
| FB | AI 프로세스 종료 | 폴백 없음, 값 그대로 | stale 폴백 → 기준값, ignored, resume 뒤 accept |

참고 값: 6 NTU 기준값 18.18, 60 NTU 기준값 25.75, 6 NTU AI 추천값 17.17 (학습 결과에 따라 조금 다를 수 있다).

- 결과: `logs/verify_security.json`. 모드별 로그 사본은 `.txt`(`logs/verify_security_{off,on}_{gateway,plc2,gateway_console}.txt`)와
  `_process.csv`로 남긴다. `.log`로 두면 `verify_system.py`의 오류 줄 검사(`logs/*.log`)에 잡힌다.
- 2026-11-29 실행: `verify_security.py` 18/18, `verify_system.py` 14/14 PASS.
- 장치 로그의 Traceback 1개(`ConnectionRefusedError`)는 시작할 때 서버가 아직 안 떠서 생기는 처음 값 쓰기 재시도다.

## 알려진 한계

- **Mininet 호스트는 파일 시스템을 함께 쓴다.** attacker 를 포함한 어느 노드든 `logs/ai_predictions.jsonl`에 패킷을 쓰거나
  `logs/gateway_mode.txt`에 `ai`를 써서 resume 을 흉내 낼 수 있다. `approved_models.json`도 고칠 수 있다.
  네트워크가 아니라 시뮬레이션 구조의 한계다(실제로는 AI→게이트웨이 경로를 별도 채널·인증으로 둬야 한다).
- EtherNet/IP 에는 인증이 없다. 방화벽은 출발지 IP 로만 막으므로, hmi 를 장악하면(H3) PLC2 자체 검사만 남는다.
- ⑧(1시간 누적)은 게이트웨이가 공정 1시간 넘게 돌아야 기록이 생긴다. H4(−0.3/5분 = −3.6/시간)는 ⑧이 아니라 ⑦이 잡는다.
- 공정이 조류를 계산하지 않아, ⑦의 조류 첫날 시나리오는 실행 중인 시스템에서 재현되지 않는다.
- 운영자 수동 모드가 없다. 게이트웨이가 AI 모드면 `hmi.py dose`로 넣은 값을 다음 통과 패킷(공정 5분 안)이 덮어쓰고,
  폴백이면 매 주기 기준값이 덮어쓴다. 보안을 켜 두고 AI만 끄면 공정 15분 뒤 폴백한다. 직접 정하려면 둘 다 끄고 띄운다.
- `ai_agent.py pause`/`resume`은 `DIRECT_WRITE = True`일 때만 뜻이 있어 지금은 효과가 없다(패킷은 계속 나간다).
