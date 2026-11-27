# 정수장 약품 주입 구간 시뮬레이션 (MiniCPS)

정수장에서 응집제(물속 흙 입자를 뭉치게 하는 약품)를 넣는 구간의 **물리 계층**과 **제어 계층**을 [MiniCPS](https://github.com/scy-phy/minicps) 위에 만든 학교 프로젝트입니다.

- 물리 계층: 물의 상태를 계산하는 공정 프로그램 하나. 착수정 수위, 약품을 넣은 뒤의 pH, 5시간 뒤 침전수 탁도를 계산합니다.
- 제어 계층: 센서를 읽고 펌프와 밸브를 움직이는 PLC 세 대와 SIS(PLC와 따로 두는 안전 장치).
- HMI: 값을 보여 주고 설정값을 넣는 최소한의 화면.

AI 주입률 예측은 아래 "AI 에이전트" 절에 있습니다. 게이트웨이, 구역·방화벽 같은 나머지 "스마트 정수장" 부분은 다음 단계이고, 여기에는 없습니다.

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
   ├─ ai_model.py  ai_agent.py                               AI 추론과 에이전트
   ├─ dataset/             학습 데이터 생성, 학습, 평가 (아래 "AI 에이전트" 절)
   └─ tests/
      ├─ verify_physical_process.py  공정만 1,500걸음 대본 검증
      ├─ verify_control_logic.py     SIS·PLC3 판정에 센서값을 순서대로 넣어 봄
      └─ verify_system.py            전체를 띄우고 확인 1~6 자동 실행
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
sudo apt install -y mininet openvswitch-switch sqlite3 git
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
sudo python3 run.py                         # 가상 네트워크와 장치 다섯을 띄우고 Mininet 명령창을 연다
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

물리·제어 계층 위에 AI 추론 계층을 붙였습니다. 원수 탁도를 보고 약품 주입률 설정값을 정해 PLC2에 전달합니다. 운영자는 하루 한 번 자-테스트를 해서 그 사이에는 설정값이 물 상태를 따라가지 못하는데, AI는 공정 1분마다 다시 정합니다.

### 추가된 파일

| 파일 | 역할 |
|---|---|
| `water_cps/scenarios.py` | 원수 시나리오 (평상시·호우·고탁도·조류). 30일, 호우 3번, 고탁도 1번, 조류 1번 |
| `water_cps/operator_policy.py` | 운영자 정책 흉내 (하루 1회 자-테스트, 탁도 급등 시 비상 주입) |
| `water_cps/dataset/build_dataset.py` | 공정 수식을 빠르게 돌려 학습 데이터 CSV 생성 |
| `water_cps/dataset/train_model.py` | scikit-learn 2단계 모델 학습과 평가 |
| `water_cps/dataset/evaluate_policy.py` | 운영자와 AI를 같은 30일 시나리오로 돌려 비교 |
| `water_cps/ai_model.py` | 모델 불러오기와 추론, AI1·AI2 판단 (학습·평가·에이전트가 같이 씀) |
| `water_cps/ai_agent.py` | 실행 중인 시스템에서 AI 추론 → PLC2 설정값 전달 |
| `water_cps/scenario_feeder.py` | 실행 중인 시스템에 원수 시나리오를 흘려 넣음 |

### 모델

- 1단계: 입력 특성 → 수질 유형 분류 (RandomForest)
- 2단계: 유형별로 자-테스트 필요 주입률 예측 (평상시 Ridge, 호우·조류 RandomForest)
- 설정값 = 예측한 필요 주입률 + 여유 2 mg/L (운영자가 자-테스트 값에 여유를 얹는 것과 같음)

입력 특성은 탁도계가 읽은 탁도, log(탁도), 탁도 변화율(1시간 전 대비 log 비), 조류 보정값입니다. 학습 데이터의 탁도계 값에는 ±2% 측정 오차를 넣었습니다. 호우와 고탁도는 탁도만으로는 겹치므로(둘 다 50~400 NTU) 변화율로 구분합니다. 호우는 1시간에 몇 배씩 오르내리고, 고탁도는 천천히 올라 오래 머뭅니다. 학습 목표는 운영자 설정값이 아니라 그 시각에 자-테스트를 했다면 나왔을 값(참 필요 주입률 + 측정 오차 0.5 mg/L)입니다. 하루 한 번만 바뀌는 운영자 설정값을 따라 배우면 모델도 늦게 반응하는 운영자를 흉내 낼 뿐이기 때문입니다. 검증용 20%는 유형 비율을 지켜 무작위로 떼어 둡니다.

### 결과 (2026-11-27)

`train_model.py` 검증 데이터: 유형 분류 정확도 0.993 (고탁도 0.96, 호우 0.95), 유형별 필요 주입률 예측 오차(MAE) 0.04~0.15 mg/L. 평소 상황인 검증 데이터에서 AI1은 0%, AI2는 1.25% 걸립니다(대부분 호우와 고탁도가 겹치는 구간).

`evaluate_policy.py`, 30일 시나리오 (탁도계 측정 오차는 학습 때와 다른 난수):

| 정책 | 침전수 1 NTU 초과 시간 | 최대 침전수 탁도 | PLC3 경보 | 약품 사용량 |
|---|---|---|---|---|
| 운영자 (하루 1회 자-테스트) | 0.14% | 5.88 NTU | 1번 | 31.8 t |
| AI (조류 정보 있음) | 0.00% | 0.75 NTU | 0번 | 26.7 t |
| AI (조류 정보 없음) | 10.00% | 1.93 NTU | 1번 | 25.9 t |

조류는 탁도를 바꾸지 않고 필요 주입률만 올리므로 탁도만 보면 알 수 없습니다. 조류 정보가 없으면 AI는 조류 기간 내내 모자라게 넣습니다. 그래서 에이전트는 조류 센서 값(`logs/algae_sensor.txt`)을 함께 읽습니다.

### 보안 조치 (`ai_agent.py`, `ai_model.py`)

F1·H2는 팀 보안 설계 문서의 위협 번호입니다. AI1·AI2는 팀 위협 H1~H5·F1에 들어가지 않는 AI 자신의 약점(처음 보는 상황, 자신 없는 예측)을 다루는 조치라 따로 번호를 붙였습니다.

- **F1 출력 범위 검사**: 설정값이 5~50 mg/L를 벗어나면 범위 끝값으로 자릅니다. 분류된 유형의 모델이 없으면 직전 설정값을 유지합니다.
- **H2 입력 이상 탐지**: 탁도가 한 번에 100 NTU 넘게 바뀌거나 1000 NTU를 넘으면 경보를 내고 그 값으로 설정값을 바꾸지 않습니다. 새 값이 5번 연속 비슷하게 읽히면(공정 약 5분) 실제 변화로 보고 받아들입니다. 시나리오의 가장 빠른 호우도 공정 1분에 7 NTU 정도 오르므로 정상 호우로는 경보가 나지 않습니다.
- **AI1 처음 보는 입력 감지**: 입력(탁도, 탁도 변화율, 조류 보정값)이 학습 데이터 범위를 조금 넓힌 범위 밖이면 모델이 모르는 상황으로 봅니다. 입력을 범위 끝으로 잘라 예측하고, 그 값과 직전 설정값 중 큰 쪽을 씁니다. 즉 모르는 상황에서는 주입률을 줄이지 않고 운영자 확인 경보를 냅니다. 처음에는 직전 값을 그대로 유지하게 만들었는데, 학습 때보다 빠른 호우(탁도 6 → 600 NTU)에서 17 mg/L에 머물러 크게 모자라게 넣는 것을 확인하고 바꿨습니다. 팀 설계의 "처음 보는 조건에서는 물러남"에 해당합니다.
- **AI2 예측 불확실성 감시**: 분류기가 유형을 확신하지 못하면(가장 높은 확률 < 0.8) 상위 두 유형의 모델로 각각 예측해 큰 값을 씁니다. 회귀 RandomForest 나무들의 예측이 평소보다 크게 갈리면(학습 데이터 99.9 백분위수의 1.2배 초과) 그 편차의 2배를 더 넣습니다. 약품은 모자랄 때가 더 위험하므로 자신 없으면 넉넉한 쪽으로 갑니다.
- AI1 범위와 AI2 기준은 `train_model.py`가 학습 데이터로 정해 `dataset/models/guard.json`에 저장합니다. `evaluate_policy.py`도 에이전트와 같은 AI1·AI2·F1 판단으로 돌립니다.

### 학습 데이터 생성, 학습, 평가

생성된 CSV와 pkl 파일은 gitignore 처리돼 있습니다. 아래 명령으로 다시 만듭니다.

```bash
cd ~/water_cps
venv/bin/pip install scikit-learn
venv/bin/python dataset/build_dataset.py       # dataset/train.csv (1초 안쪽)
venv/bin/python dataset/train_model.py         # dataset/models/*.pkl
venv/bin/python dataset/evaluate_policy.py     # 운영자와 AI 비교표
```

### 실행

`run.py` 맨 위의 설정으로 무엇을 띄울지 정합니다.

| 설정 | 기본값 | 뜻 |
|---|---|---|
| `USE_AI_AGENT` | True | hmi 노드에서 화면(hmi.py)과 함께 AI 에이전트를 띄움 |
| `FEED_SCENARIO` | True | 원수 공급기를 띄워 호우·조류 시나리오를 흘려 넣음 |
| `FEED_START_HOURS` | 119.5 | 시나리오 시작 시각. 119.5면 5일차 호우 30분 전이라 실제 30초 뒤 호우가 옴 |

```bash
cd ~/water_cps
sudo python3 run.py
```

```bash
tail -f ~/water_cps/logs/ai_agent.log            # 다른 터미널: AI 판단
watch -n 1 cat ~/water_cps/logs/hmi_screen.txt   # 다른 터미널: HMI 화면
```

Mininet 명령창에서 넣는 명령:

```
mininet> hmi venv/bin/python ai_agent.py status    지금 탁도로 AI 예측값 한 번 보기
mininet> hmi venv/bin/python ai_agent.py pause     AI 자동 제어 멈춤
mininet> hmi venv/bin/python hmi.py dose 20        (멈춘 뒤) 운영자가 직접 설정
mininet> hmi venv/bin/python ai_agent.py resume    AI 자동 제어 다시 시작
```

AI를 멈추지 않고 `hmi.py dose`를 넣으면 1초 안에 AI가 자기 값으로 덮어씁니다.

`tests/verify_system.py`는 AI와 원수 공급기 없이 기존 구성을 시험합니다(`start_devices`의 기본값).

### AI 부분의 한계

- 공정(`physical_process.py`)은 조류를 계산하지 않습니다. 그래서 실행 중인 시스템에서는 조류 기간에 AI가 주입률을 올려도 공정에는 과주입으로만 나타납니다. 조류 효과는 `evaluate_policy.py`에서만 확인할 수 있습니다.
- H2 입력 이상 탐지는 한 번에 크게 바뀌는 값만 잡습니다. 100 NTU보다 작게 조금씩 바꾸는 위변조는 잡지 못합니다.
- 탁도계 측정 오차는 학습 데이터와 평가에만 넣었습니다. 실행 중인 시스템에서는 공정이 상태 표의 원수 탁도를 참값으로 쓰므로 원수 공급기가 오차 없는 값을 씁니다.
- 측정 오차를 넣어도 공정 수식이 단순해서 모델 오차가 매우 작습니다. 실제 정수장 데이터는 이보다 훨씬 흩어져 있을 것입니다.
- AI1은 특성마다 따로 범위를 봅니다. 각 값은 범위 안인데 조합이 처음 보는 경우(예: 조류 기간의 호우)는 잡지 못합니다.
- AI1에서 탁도가 학습 범위를 크게 넘으면 예측이 범위 끝 값에 머뭅니다. 약 800 NTU를 넘으면 여유 2 mg/L가 줄어들기 시작하므로 운영자 확인이 필요합니다(그래서 경보를 냅니다).
- AI2는 넉넉한 쪽으로 가므로 과주입이 생길 수 있습니다. 30일 평가에서 침전수 탁도 최댓값이 0.53에서 0.75 NTU로 올랐습니다(기준 1.0 이내).
- 이 시뮬레이션에서 나무 편차는 대부분 자-테스트 측정 오차를 반영해, AI2 나무 편차 검사는 거의 걸리지 않습니다(30일 중 5걸음).

## 일부러 남긴 한계

설계 문서 11-2절에 자세히 적었습니다.

- PLC2는 받은 설정값을 검사하지 않습니다. AI 에이전트(`ai_agent.py`)가 보내는 값은 에이전트 쪽에서 5~50 mg/L로 자르지만(F1), 다른 곳에서 PLC2에 쓰는 값은 여전히 검사하지 않습니다.
- 장치 프로그램이 죽어도 알리지 않습니다. 알아채는 일은 감시 층의 몫입니다.
- 통신에 인증이 없습니다(EtherNet/IP와 MiniCPS의 한계). 구역·방화벽 단계에서 출발지 주소로 제한합니다.
