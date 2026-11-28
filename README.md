# 정수장 약품 주입 구간 시뮬레이션 (MiniCPS)

정수장에서 응집제(물속 흙 입자를 뭉치게 하는 약품)를 넣는 구간의 **물리 계층**과 **제어 계층**을 [MiniCPS](https://github.com/scy-phy/minicps) 위에 만든 학교 프로젝트입니다.

- 물리 계층: 물의 상태를 계산하는 공정 프로그램 하나. 착수정 수위, 약품을 넣은 뒤의 pH, 5시간 뒤 침전수 탁도를 계산합니다.
- 제어 계층: 센서를 읽고 펌프와 밸브를 움직이는 PLC 세 대와 SIS(PLC와 따로 두는 안전 장치).
- HMI: 값을 보여 주고 설정값을 넣는 최소한의 화면.

AI 주입률 예측은 아래 "AI 에이전트" 절에, 게이트웨이·방화벽·PLC2 보호는 "보안 조치" 절에 있습니다. 게이트웨이는 단순 버전이라 설계의 구역 분리(감시 구역, 운전 기록 서버·복제본·중계 서버)는 만들지 않았습니다.

## 무엇이 무엇을 하나

| 블록 | 받는 값 | 하는 일 | 내는 값 |
|---|---|---|---|
| 공정 | 원수 탁도(밖에서), 취수 펌프·약품 펌프 명령, 차단밸브 | 공정 30초마다 수위, pH, 5시간 뒤 침전수 탁도 계산 | 수위, 생산 유량, 주입 후 pH, 침전수 탁도 |
| PLC1 | 수위 | 3.3 m 이하면 취수 펌프 강, 3.7 m 이상이면 약 | 취수 펌프 명령 |
| PLC2 | 생산 유량, 주입률 설정값(밖에서) | 주입량 = 유량 × 주입률 | 약품 펌프 명령 |
| PLC3 | 침전수 탁도 | 1.0 NTU를 공정 30분 넘게 넘으면 경보 | 탁도 경보 |
| SIS | 주입 후 pH | 5.8~8.5를 2주기 연속 벗어나면 차단밸브를 닫음 | 차단밸브 |
| HMI | 네 장치의 값 | 글자 화면을 만들고 설정값을 전달 | 주입률 설정값, SIS 복귀 요청 |

예: 주입률 설정값이 17 mg/L이면 PLC2는 2,000 m³/h × 17 ÷ 1000 = 34 kg/h를 명령하고, 공정은 pH 7.145를 냅니다. 원수 탁도가 6에서 300 NTU로 오르면 정확히 공정 5시간 뒤 침전수 탁도가 0.49에서 15.14 NTU로 오르고, 그 30분 뒤 PLC3가 경보를 냅니다.

시간은 60배 빠르게 흐릅니다(실제 1초 = 공정 1분). 공정 5시간은 실제 약 5분입니다.

## 폴더 구조

```
.
├─ README.md
├─ 정수장_도메인_분석_문서.html        도메인 분석 (근거 문서)
├─ outputs/
│  ├─ 261117-MiniCPS_제어공정_설계.md   설계 문서 (명세, 시험 기준, 측정 결과)
│  └─ 261124-코드정리.html              코드 설명, 학습 데이터 만드는 법
└─ water_cps/
   ├─ config.py            상수, 태그, 주소, 처음 값, 읽은 값 변환
   ├─ init.py              상태 표(SQLite) 새로 만들기
   ├─ physical_process.py  공정 (물리 계층)
   ├─ plc1.py  plc2.py  plc3.py  sis.py   제어 계층
   ├─ hmi.py               화면 갱신과 명령
   ├─ run.py               Mininet 가상 네트워크 구성과 장치 실행
   ├─ scenarios.py  operator_policy.py  scenario_feeder.py   원수 시나리오와 운영자 흉내
   ├─ ai_model.py  ai_agent.py  baseline.py                  AI 추론, 에이전트, 기준값
   ├─ gateway.py           AI 예측값 검사 후 PLC2에 쓰는 게이트웨이 (아래 "보안 조치" 절)
   ├─ dataset/             학습 데이터 생성, 학습, 평가 (아래 "AI 에이전트" 절)
   └─ tests/
      ├─ verify_physical_process.py  공정만 1,500걸음 대본 검증
      ├─ verify_control_logic.py     SIS·PLC3 판정에 센서값을 순서대로 넣어 봄
      ├─ verify_system.py            전체를 띄우고 확인 1~6 자동 실행
      ├─ test_gateway_offline.py  test_plc2_offline.py   게이트웨이·PLC2 보호 판정 (가짜 통신)
      ├─ check_firewall.py           방화벽 접속 행렬
      └─ verify_security.py          공격 시나리오를 보안 끔/켬으로 비교
```

## 필요한 환경

리눅스에서만 돕니다(MiniCPS의 통신 모듈이 리눅스가 아니면 오류를 냅니다). 아래 구성에서 확인했습니다.

- WSL2 Ubuntu 26.04 (커널 6.6.114)
- Mininet 2.3.0, Open vSwitch 3.7.1
- 장치용 Python 3.12 가상환경: MiniCPS(GitHub master), cpppo 5.2.5
- `run.py`용 시스템 Python 3.14

cpppo는 5.2.5를 씁니다. MiniCPS가 고정해 둔 4.3.4는 Python 3.12에서 import하자마자 실패합니다. PyPI의 `minicps` 1.1.3은 Python 2용이라 GitHub에서 설치합니다.

## 설치

```bash
sudo apt install -y mininet openvswitch-switch sqlite3 git iptables    # iptables 는 보안 조치용 (WSL 기본 설치에 없음)
sudo service openvswitch-switch start
sudo mn --switch ovsbr --controller none --test pingall    # 0% dropped 이면 통과
```

코드는 Windows 폴더가 아니라 리눅스 폴더(`~/water_cps`)에 두고 돌립니다. Windows 폴더 위에서는 여러 프로세스가 SQLite 파일을 함께 쓸 때 잠금 문제가 나기 쉽습니다.

```bash
mkdir -p ~/water_cps && cp -r water_cps/* ~/water_cps/ && cd ~/water_cps
uv venv --python 3.12 venv
uv pip install --python venv/bin/python "cpppo==5.2.5" pymodbus pandas "git+https://github.com/scy-phy/minicps"
venv/bin/python -c "import minicps.devices; print('ok')"
```

## 실행

```bash
cd ~/water_cps
sudo python3 run.py                         # 가상 네트워크와 호스트 일곱(plc1~3, sis, hmi, ai, attacker)을 띄우고 Mininet 명령창을 연다
```

```bash
watch -n 1 cat ~/water_cps/logs/hmi_screen.txt    # 다른 터미널에서 HMI 화면 보기
```

Mininet 명령창에서 넣는 명령:

```
mininet> hmi venv/bin/python hmi.py dose 20       주입률 설정값을 20 mg/L로
mininet> hmi venv/bin/python hmi.py sis reset     SIS 차단밸브 복귀 요청
mininet> exit                                     끝내기 (장치와 가상 네트워크를 정리한다)
```

원수 탁도는 이 계층이 정하지 않는 바깥 값입니다. 바꿔 보려면 상태 표를 직접 고칩니다.

```bash
sudo sqlite3 ~/water_cps/water_cps.sqlite "UPDATE water_cps SET value='300' WHERE name='AIT101'"
```

기록은 `~/water_cps/logs/`에 남습니다. `process.csv`가 공정 30초마다 한 줄이고, 장치마다 `<이름>.log`가 있습니다.

`run.py`가 오류로 중간에 끝나 가상 네트워크가 남았으면 `sudo mn -c`로 지웁니다.

## 검증

```bash
cd ~/water_cps
venv/bin/python tests/verify_physical_process.py     # 공정만. 관리자 권한 없이 몇 초
venv/bin/python tests/verify_control_logic.py        # SIS·PLC3 판정. 1초
sudo python3 tests/verify_system.py                  # 전체. 약 9분
```

셋 다 끝에 `RESULT=PASS`가 찍히면 통과입니다. 2026-11-19 마지막 실행에서 셋 모두 통과했습니다. 전체 검증은 14개 항목, 제어 판정은 22개입니다. 이 결과는 codex와 Claude 검토자의 코드 검토 지적을 반영한 뒤의 것입니다. 측정값은 설계 문서 10절에 있습니다.

## AI 에이전트 (스마트 정수장)

물리·제어 계층 위에 AI 추론 계층을 붙였습니다. 원수 탁도를 보고 약품 주입률 추천값을 공정 5분마다 냅니다. 팀 설계(중간발표 2부, 도메인 분석 발표 5부)를 따릅니다. 이 단계의 범위는 약품 주입 한 구간입니다.

### 추가된 파일

| 파일 | 역할 |
|---|---|
| `water_cps/scenarios.py` | 원수 시나리오. 30일에 호우 3번, 고탁도 1번, 조류 1번 (도메인 분석 문서 4-6절의 사전 시뮬레이션 배치를 따름) |
| `water_cps/operator_policy.py` | 운영자 흉내 (매일 9시 자-테스트 + 여유 2 mg/L, 100 NTU 넘으면 비상 주입). 비교 기준 |
| `water_cps/dataset/build_dataset.py` | 공정 수식을 빠르게 돌려 학습 데이터 CSV 생성 |
| `water_cps/dataset/train_model.py` | 2단계 모델 학습·평가, AI1·AI2 기준, 모델 버전·해시, 기준값 조견표 생성 |
| `water_cps/dataset/evaluate_policy.py` | 운영자·기준값·AI를 같은 30일 시나리오로 돌려 비교 |
| `water_cps/ai_model.py` | 모델 불러오기, 추론, AI1·AI2 판단, 추천 공식 (학습·평가·에이전트가 같이 씀) |
| `water_cps/baseline.py` | 기준값(폴백 값) 계산 방식: 조견표 + 자-테스트 갱신. AI 출력을 쓰지 않음 |
| `water_cps/ai_agent.py` | AI 노드. 실행 중인 시스템에서 추천값과 예측값 패킷을 냄 |
| `water_cps/scenario_feeder.py` | 실행 중인 시스템에 원수 탁도를 흘려 넣고, 매일 9시 자-테스트 값을 씀 |

### 모델

추천 주입률 = 기본 추천 − 여유 조정 + 자-테스트 보정 (팀 설계 공식)

| 부분 | 이 구현 | 설계 |
|---|---|---|
| 기본 추천 | 2단계 모델의 예측 필요 주입률 + 운영자 여유 2 mg/L | 운영자 수준을 5분마다 |
| 여유 조정 | 0 (`ai_model.MARGIN_REDUCTION`) | 운전하며 다시 배우는 추가 학습에서 0.5씩 넓힘. 평소 조건에서만, 호우·처음 보는 조건에서는 0 |
| 자-테스트 보정 | 하루 한 번 실험값 − 그때 모델의 예측 (−2~+10 mg/L로 자름), 다음 실험까지 유지 | 센서에 안 보이는 변화(조류 등)를 메움 |

- 1단계: 입력 → 원수 유형 분류 (RandomForest). 유형은 평상시·고탁도·호우.
- 2단계: 유형별 필요 주입률 회귀 (평상시 Ridge, 고탁도·호우 RandomForest).
- 입력: 탁도계가 읽은 탁도(±2% 측정 오차), log(탁도), 탁도 변화율(1시간 전 대비 log 비, 설계의 "직전 값(lag)").
- 정답: 시뮬레이터의 필요 주입률에 자-테스트 측정 오차(0.5 mg/L)를 더한 값.
- 호우와 고탁도는 탁도 범위가 겹쳐(50~400 NTU) 변화율로 구분합니다. 호우는 1시간에 몇 배씩 오르내리고, 고탁도는 천천히 올라 오래 머뭅니다.
- 조류는 팀 설계대로 센서에 안 보이는 변화로 둡니다. 조류 기간은 센서값이 평상시와 같은데 필요량만 5 mg/L 많아서, 학습에서 빼고 자-테스트 보정으로 메웁니다. 조류 유형 분류는 설계상 pH 등의 흔적으로 하는데, 지금 공정이 원수 탁도만 만들어 구현하지 않았습니다.
- 검증용 20%는 유형 비율을 지켜 무작위로 떼어 둡니다(시간 정보를 입력으로 쓰지 않으므로 시간 순서가 새지 않음).

### 기준값 (`baseline.py`)

팀 설계에서 "기준값의 계산 방식은 모델 담당, 계산 위치와 전환 조건은 보안 담당"입니다. 이 파일은 계산 방식만 정합니다.

- 학습 기록을 원수 탁도 구간(0·10·20·50·100·200·500 NTU)으로 나누고, 구간마다 자-테스트 값의 95 백분위수 + 2 mg/L. 기록이 없는 구간은 아래 구간 + 2 mg/L.
- 하루 한 번 자-테스트로 갱신: 실험값 + 2가 표보다 높을 때만 올림 (약품은 넉넉한 쪽).
- AI 출력을 입력으로 받지 않습니다. `train_model.py`가 `dataset/baseline_table.json`을 만들고, `venv/bin/python baseline.py 120`처럼 값을 확인할 수 있습니다.

### 예측값 패킷 (`ai_agent.py`)

중간발표 21쪽 형식으로, 추천을 낼 때마다 `logs/ai_predictions.jsonl`에 한 줄씩 씁니다.

```json
{"process": "chemical", "setpoint": 22.13, "unit": "mg/L", "created_at": "2026-11-27T14:22:03+00:00",
 "seq": 1795789322121, "model_version": "v20261127-232144", "model_hash": "d0ed35fb…",
 "inputs": {"turbidity_ntu": 6.23, "turbidity_trend": -0.038, "sim_time": 41760.0},
 "water_type": "normal", "confidence": 1.0,
 "parts": {"base": 17.30, "margin_reduction": 0.0, "jar_correction": 4.83}, "flags": []}
```

- `seq`는 재시작해도 줄지 않게 시작 시각(ms)에서 늘립니다.
- `model_hash`는 모델 파일 전체의 sha256입니다(`train_model.py`가 `dataset/models/model_info.json`에도 기록).
- 시각·순번·승인 모델·입력 원본 검사는 게이트웨이(보안 설계 담당)가 합니다.
- 팀 설계는 "AI는 PLC에 바로 쓰지 못한다"입니다. `ai_agent.py`의 `DIRECT_WRITE = False`라 AI는 패킷만 쓰고, PLC2에는 게이트웨이(`gateway.py`)가 검사를 통과한 값만 씁니다(아래 "보안 조치").

### 결과 (2026-11-27)

`train_model.py` 검증 데이터: 유형 분류 정확도 0.992 (고탁도 0.96, 호우 0.95), 유형별 필요 주입률 예측 오차(MAE) 0.04~0.15 mg/L. 평소 상황인 검증 데이터에서 AI1은 0%, AI2는 1.26% 걸립니다(대부분 호우와 고탁도가 겹치는 구간).

`evaluate_policy.py`, 30일 시나리오 (탁도계 측정 오차는 학습 때와 다른 난수, 자-테스트는 매일 9시):

| 정책 | 침전수 1 NTU 초과 | 그중 조류 기간 | 최대 침전수 탁도 | PLC3 경보 | 약품 사용량 |
|---|---|---|---|---|---|
| 운영자 (매일 자-테스트) | 14.5시간 | 9.0시간 | 5.88 NTU | 5번 | 27.9 t |
| 기준값 (조견표 + 자-테스트) | 9.0시간 | 9.0시간 | 1.31 NTU | 1번 | 28.1 t |
| AI (보정 없음) | 72.0시간 | 72.0시간 | 1.92 NTU | 1번 | 25.9 t |
| AI (자-테스트 보정) | 9.0시간 | 9.0시간 | 1.58 NTU | 1번 | 26.7 t |

- 호우·고탁도: AI는 초과 0시간입니다. 운영자는 자-테스트 사이에 탁도가 바뀌어 5.5시간 초과했습니다.
- 조류: 보정 없는 AI는 조류 3일 내내 모자라게 넣습니다(72시간). 자-테스트 보정을 넣으면 첫날 9시간(다음 실험 전까지)만 남습니다. 운영자·기준값도 같은 9시간입니다. 팀 설계의 "남는 위험: 센서에 안 보이는 변화의 첫날"과 같습니다.
- 약품: 자-테스트 보정 AI가 운영자보다 1.2 t(4%) 적게 씁니다. 여유 조정이 0이라 절감은 호우 대응에서만 나옵니다.
- AI(자-테스트 보정)와 기준값의 RMSE는 1.25 mg/L이고, 기준값보다 3.5 mg/L 넘게 낮은 시간은 0.22%입니다(게이트웨이 ⑦ 거부 대상, 조류 첫날).

### 보안 조치 (`ai_agent.py`, `ai_model.py`)

F1·H2는 팀 보안 설계 문서의 위협 번호입니다. AI1·AI2는 팀 위협 H1~H5·F1에 들어가지 않는 AI 자신의 약점(처음 보는 상황, 자신 없는 예측)을 다루는 조치라 따로 번호를 붙였습니다.

- **F1 출력 범위 검사**: 추천값이 5~50 mg/L를 벗어나면 범위 끝값으로 자릅니다. 분류된 유형의 모델이 없으면 직전 추천값을 유지합니다.
- **H2 입력 이상 탐지**: 탁도가 한 번에 100 NTU 넘게 바뀌거나 1000 NTU를 넘으면 경보를 내고 그 값으로 추천을 바꾸지 않습니다. 새 값이 5번 연속 비슷하게 읽히면(공정 약 5분) 실제 변화로 보고 받아들입니다. 시나리오의 가장 빠른 호우도 공정 1분에 7 NTU 정도 오르므로 정상 호우로는 경보가 나지 않습니다.
- **AI1 처음 보는 입력 감지**: 입력(탁도, 탁도 변화율)이 학습 데이터 범위를 조금 넓힌 범위 밖이면 모델이 모르는 상황으로 봅니다. 입력을 범위 끝으로 잘라 예측하고, 그 값과 직전 추천값 중 큰 쪽을 씁니다. 즉 모르는 상황에서는 주입률을 줄이지 않고 운영자 확인 경보를 냅니다. 처음에는 직전 값을 그대로 유지하게 만들었는데, 학습 때보다 빠른 호우(탁도 6 → 600 NTU)에서 17 mg/L에 머물러 크게 모자라게 넣는 것을 확인하고 바꿨습니다. 팀 설계의 "처음 보는 조건에서는 물러남"에 해당합니다.
- **AI2 예측 불확실성 감시**: 분류기가 유형을 확신하지 못하면(가장 높은 확률 < 0.8) 상위 두 유형의 모델로 각각 예측해 큰 값을 씁니다. 회귀 RandomForest 나무들의 예측이 평소보다 크게 갈리면(학습 데이터 99.9 백분위수의 1.2배 초과) 그 편차의 2배를 더 넣습니다. 약품은 모자랄 때가 더 위험하므로 자신 없으면 넉넉한 쪽으로 갑니다.
- AI1 범위와 AI2 기준은 `train_model.py`가 학습 데이터로 정해 `dataset/models/guard.json`에 저장합니다. `evaluate_policy.py`도 에이전트와 같은 판단 순서(AI1·AI2 → 추천 공식 → AI1 줄이지 않음 → F1)로 돌립니다.

### 학습 데이터 생성, 학습, 평가

생성된 CSV, 모델 파일, 기준값 표는 gitignore 처리돼 있습니다. 아래 명령으로 다시 만듭니다.

```bash
cd ~/water_cps
venv/bin/pip install scikit-learn
venv/bin/python dataset/build_dataset.py       # dataset/train.csv (1초 안쪽)
venv/bin/python dataset/train_model.py         # dataset/models/, dataset/baseline_table.json
venv/bin/python dataset/evaluate_policy.py     # 운영자·기준값·AI 비교표
```

### 실행

`run.py` 맨 위의 설정으로 무엇을 띄울지 정합니다.

| 설정 | 기본값 | 뜻 |
|---|---|---|
| `USE_AI_AGENT` | True | ai 노드(192.168.1.60)에서 AI 에이전트를 띄움. 패킷만 내고 PLC2에는 게이트웨이가 씀 |
| `USE_SECURITY` | True | iptables, 게이트웨이 검사, PLC2 보호를 함께 켬. False 이면 셋 다 끔(게이트웨이는 검사 없이 그대로 전달). 아래 "보안 조치" |
| `FEED_SCENARIO` | True | 원수 공급기를 띄워 원수 탁도와 매일 9시 자-테스트 값을 흘려 넣음 |
| `FEED_START_HOURS` | 119.5 | 시나리오 시작 시각. 119.5면 5일차 호우 30분 전이라 실제 30초 뒤 호우가 옴. 215.5면 9일차 고탁도, 431.5면 18일차 조류 |

```bash
cd ~/water_cps
sudo python3 run.py
```

```bash
tail -f ~/water_cps/logs/ai_agent.log            # 다른 터미널: AI 판단
tail -f ~/water_cps/logs/gateway.log             # 다른 터미널: 게이트웨이 판정 (JSON 한 줄씩)
watch -n 1 cat ~/water_cps/logs/hmi_screen.txt   # 다른 터미널: HMI 화면
```

Mininet 명령창에서 넣는 명령:

```
mininet> ai venv/bin/python ai_agent.py status         지금 탁도로 AI 추천값 한 번 보기
mininet> ai venv/bin/python ai_agent.py jartest 20.5   운영자가 자-테스트 결과 입력
mininet> hmi venv/bin/python gateway.py resume         게이트웨이 폴백 → AI 모드 복귀 (운영자 승인)
```

- 보안을 켜면 ai 노드는 PLC2에 닿지 못해 `status`의 "PLC2 현재 설정값"이 nan 으로 나옵니다(설계대로).
- `ai_agent.py pause`/`resume`은 AI가 PLC2에 직접 쓸 때(`DIRECT_WRITE = True`)만 뜻이 있는 명령이라 지금은 효과가 없습니다. 패킷은 계속 나갑니다.
- `hmi.py dose`로 넣은 값은 게이트웨이가 다음 통과 패킷(공정 5분 안, 실제 약 5초)에서 덮어쓰고, 폴백 중에는 매 주기 기준값으로 덮어씁니다. 지금은 운영자 수동 모드가 없습니다. 직접 정하려면 `USE_AI_AGENT`와 `USE_SECURITY`를 둘 다 끄고 띄웁니다.

`tests/verify_system.py`는 AI와 원수 공급기 없이 기존 구성을 시험합니다(`start_devices`의 기본값).

### 팀 설계와 아직 다른 점

- 입력 센서: 설계는 원수 탁도·pH·수온·전기전도도인데, 공정이 원수 탁도만 만들어 탁도와 변화율만 씁니다. 조류 유형 분류도 그래서 없습니다.
- 읽는 곳: 설계는 감시 구역의 기록 복제본에서 읽는데, 구역이 아직 없어 PLC1에서 직접 읽습니다. 구역이 생기면 읽는 주소만 바꿉니다.
- 쓰는 곳: 설계대로 AI는 PLC2에 쓰지 않고 게이트웨이를 거칩니다. 설계의 중계 서버 대신 예측값 파일(`logs/ai_predictions.jsonl`)을 씁니다.
- 여유 조정(추가 학습)과 14일 재학습·섀도 운전은 구현하지 않았습니다.
- 학습 데이터는 MiniCPS 운전 기록이 아니라 같은 공정 수식을 빠르게 돌려 만듭니다.
- 소독·착수정 모델은 없습니다(이 단계 범위는 약품 주입 한 구간).

### AI 부분의 한계

- 조류 첫날: 자-테스트가 하루 한 번이라 다음 실험 전까지 모자라게 넣습니다(9시간). 또 조류가 끝난 뒤 다음 실험까지는 보정이 남아 약간 넘치게 넣습니다(조류 기간을 뺀 최대 침전수 탁도 0.81 NTU, 기준 1.0 이내). 공정(`physical_process.py`)이 조류를 계산하지 않아 실행 중인 시스템에서는 조류 효과가 나타나지 않습니다. 조류 영향은 `evaluate_policy.py`에서 확인합니다.
- H2 입력 이상 탐지는 한 번에 크게 바뀌는 값만 잡습니다. 100 NTU보다 작게 조금씩 바꾸는 위변조는 잡지 못합니다(게이트웨이 ⑦·⑧의 몫).
- 탁도계 측정 오차는 학습 데이터와 평가에만 넣었습니다. 실행 중인 시스템에서는 공정이 상태 표의 원수 탁도를 참값으로 쓰므로 원수 공급기가 오차 없는 값을 씁니다.
- 측정 오차를 넣어도 공정 수식이 단순해서 모델 오차가 매우 작습니다. 실제 정수장 데이터는 이보다 훨씬 흩어져 있을 것입니다.
- AI1은 특성마다 따로 범위를 봅니다. 각 값은 범위 안인데 조합이 처음 보는 경우(예: 낮은 탁도에서 큰 변화율)는 잡지 못합니다.
- AI1에서 탁도가 학습 범위를 크게 넘으면 예측이 범위 끝 값에 머뭅니다. 약 800 NTU를 넘으면 여유 2 mg/L가 줄어들기 시작하므로 운영자 확인이 필요합니다(그래서 경보를 냅니다).
- 이 시뮬레이션에서 나무 편차는 대부분 자-테스트 측정 오차를 반영해, AI2 나무 편차 검사는 거의 걸리지 않습니다.

## 보안 조치

팀 보안 설계(중간발표 22·28쪽)와 AI 파트의 「게이트웨이 구현 안내」를 따라 약품 주입 구간에 세 겹의 보호를 붙였습니다. 결정 사항과 시험 기준의 자세한 내용은 `CLAUDE.md`에 있습니다.

### 구성

`run.py`의 `USE_SECURITY` 하나로 셋을 함께 켜고 끕니다. 끄면 보호가 없는 비교용 구성이 됩니다(게이트웨이는 검사 없이 그대로 전달, 방화벽 없음, PLC2 검사 없음). `start_devices`의 기본값은 끔이라 `verify_system.py`는 예전 구성을 시험합니다.

| 호스트 | IP | 띄우는 것 |
|---|---|---|
| plc1, plc2, plc3, sis | 192.168.1.10~40 | 제어 장치 |
| hmi | 192.168.1.50 | 화면(hmi.py), 게이트웨이(gateway.py) |
| ai | 192.168.1.60 | AI 에이전트. 패킷만 쓰고 PLC2에는 쓰지 않음 |
| attacker | 192.168.1.70 | 공격 시험용. 아무것도 띄우지 않음 |

**방화벽**(장치마다 iptables): `lo`와 이미 연 연결의 응답은 허용하고, EtherNet/IP(44818) 새 연결은 아래 출발지만 받습니다. 걸린 규칙은 `logs/firewall_rules.txt`에 남습니다.

| 서버 | 받는 출발지 |
|---|---|
| plc1 | plc2, hmi, ai |
| plc2, plc3, sis | hmi |

**게이트웨이**(`gateway.py`, hmi 노드): AI 예측값 패킷을 8가지로 검사하고 통과한 값만 PLC2에 씁니다. 판정은 `logs/gateway.log`에 JSON 한 줄씩 남깁니다.

| 검사 | 기준 | 막는 위협 |
|---|---|---|
| ① 형식 | 필수 필드, 단위 mg/L | F1 |
| ② 출처·모델 | 모델 해시가 승인 목록(`dataset/approved_models.json`)에 있음 | H5 |
| ③ 시각·순번 | 공정 시각 차이 0~15분, seq 증가 | F1, 재전송 |
| ④ 입력 센서 | AI가 본 탁도와 PLC1 원본 차이 ≤ max(5 NTU, 10%) | H2 |
| ⑤ 범위 | 8~45 mg/L | F1 |
| ⑥ 변화량 | PLC2 현재 설정값과 차이 ≤ 5 mg/L | 급변 |
| ⑦ 기준값 | 기준값보다 3.5 mg/L 넘게 낮으면 거부 | H2, H4 |
| ⑧ 누적 | 공정 1시간 전 적용값과 차이 ≤ 10 mg/L | H4 |

연속 3번 거부, 통과한 예측이 공정 15분 없음, PLC3 침전수 탁도 경보 중 하나면 기준값(`baseline.py`)으로 폴백해 매 주기 씁니다. 내려가는 길은 자동이고 올라오는 길은 운영자 승인(`resume`)뿐입니다. 게이트웨이를 다시 띄워도 폴백은 풀리지 않습니다.

**PLC2 보호**(`plc2.py --secure`): 설정값이 5~50 mg/L 밖이면 거부하고 직전 값을 태그에 다시 씁니다(게이트웨이를 거치지 않은 쓰기에도 걸림). 실제 주입률은 공정 시각 어느 60초 구간에서도 2 mg/L 넘게 바뀌지 않게 목표값을 따라갑니다. 이 때문에 보안을 켜면 `hmi.py dose 110`으로 SIS를 일으킬 수 없습니다.

### 명령

```bash
cd ~/water_cps
venv/bin/python gateway.py approve            # 학습한 모델을 승인 목록에 넣기 (학습한 PC마다 한 번)
```

승인 목록은 모델 해시가 PC마다 달라 저장소에 넣지 않습니다. 없으면 보안을 켰을 때 모든 패킷이 ②에서 거부되어 곧 폴백합니다. 폴백에서 돌아올 때는 Mininet 명령창에서 `hmi venv/bin/python gateway.py resume`.

### 시험

| 파일 | 권한 | 시간 | 내용 |
|---|---|---|---|
| `tests/test_gateway_offline.py` | 없음 | 1초 | 가짜 PLC로 게이트웨이 검사·폴백·복귀 |
| `tests/test_plc2_offline.py` | 없음 | 1초 | PLC2 범위 거부와 ramp |
| `tests/check_firewall.py` | sudo | 1분 | 호스트 7대 → 서버 4대 접속 행렬, 방화벽 없이/건 뒤 |
| `tests/verify_security.py` | sudo | 약 12분 | 공격 시나리오를 보안 끔/켬으로 두 번 돌려 비교 |

`verify_security.py`는 정상 운전, F1(터무니없는 예측), H4(천천히 낮추기), H2(탁도 위조), H5(모델 바꿔치기), H1(attacker 호스트에서 PLC2 쓰기), H3(HMI에서 dose 200), ramp, 폴백(AI 프로세스 종료)을 돌립니다. 끔에서는 공격이 실제로 통해야, 켬에서는 막혀야 통과입니다. 2026-11-29 마지막 실행에서 `verify_security.py` 18/18, `verify_system.py` 14/14 통과했습니다.

### 보안 조치의 한계

- Mininet 호스트는 파일 시스템을 함께 씁니다. 어느 노드든 예측값 파일, 게이트웨이 모드 파일(`logs/gateway_mode.txt`), 승인 목록을 고칠 수 있어 `resume`을 흉내 낼 수 있습니다. 네트워크가 아니라 시뮬레이션 구조의 한계입니다.
- 방화벽은 출발지 IP로만 막습니다. hmi를 장악하면(H3) PLC2 자체 검사만 남습니다.
- ⑧은 게이트웨이가 공정 1시간 넘게 돌아야 기록이 생깁니다. 천천히 낮추기(공정 5분마다 −0.3)는 ⑦이 잡습니다.
- 운영자 수동 모드가 없습니다. AI가 도는 동안 `hmi.py dose`로 넣은 값은 게이트웨이가 덮어씁니다.

## 일부러 남긴 한계

설계 문서 11-2절에 자세히 적었습니다.

- 보안을 끄면(`USE_SECURITY = False`) PLC2는 받은 설정값을 검사하지 않습니다. 켜면 5~50 mg/L 밖의 값을 거부합니다(위 "보안 조치").
- 장치 프로그램이 죽어도 알리지 않습니다. 알아채는 일은 감시 층의 몫입니다.
- 통신에 인증이 없습니다(EtherNet/IP와 MiniCPS의 한계). 보안을 켜면 장치마다 방화벽으로 출발지 주소를 제한합니다(위 "보안 조치").
