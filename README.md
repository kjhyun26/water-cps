# 정수장 약품 주입 구간 시뮬레이션 (MiniCPS)

정수장에서 응집제(물속 흙 입자를 뭉치게 하는 약품)를 넣는 구간의 **물리 계층**과 **제어 계층**을 [MiniCPS](https://github.com/scy-phy/minicps) 위에 만든 학교 프로젝트입니다.

- 물리 계층: 물의 상태를 계산하는 공정 프로그램 하나. 착수정 수위, 약품을 넣은 뒤의 pH, 5시간 뒤 침전수 탁도를 계산합니다.
- 제어 계층: 센서를 읽고 펌프와 밸브를 움직이는 PLC 세 대와 SIS(PLC와 따로 두는 안전 장치).
- HMI: 값을 보여 주고 설정값을 넣는 최소한의 화면.

모델 예측, 게이트웨이, 구역·방화벽 같은 "스마트 정수장" 부분은 이 계층 위에 붙을 다음 단계이고, 여기에는 없습니다.

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
│  └─ 261124-코드정리.html              코드 설명 문서
└─ water_cps/
   ├─ config.py            상수, 태그, 주소, 처음 값, 읽은 값 변환
   ├─ init.py              상태 표(SQLite) 새로 만들기
   ├─ physical_process.py  공정 (물리 계층)
   ├─ plc1.py  plc2.py  plc3.py  sis.py   제어 계층
   ├─ hmi.py               화면 갱신과 명령
   ├─ run.py               Mininet 가상 네트워크 구성과 장치 실행
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

## 일부러 남긴 한계

설계 문서 11-2절에 자세히 적었습니다.

- PLC2는 받은 설정값을 검사하지 않습니다. 마지막 한계(5~50 mg/L)는 스마트 정수장 단계에서 PLC2에 넣습니다.
- 장치 프로그램이 죽어도 알리지 않습니다. 알아채는 일은 감시 층의 몫입니다.
- 통신에 인증이 없습니다(EtherNet/IP와 MiniCPS의 한계). 구역·방화벽 단계에서 출발지 주소로 제한합니다.
