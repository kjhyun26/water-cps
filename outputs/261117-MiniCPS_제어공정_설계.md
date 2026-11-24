# 물리·제어 계층 설계 (정수장 약품 주입 구간, MiniCPS)

기준 문서: `정수장_도메인_분석_문서.html` 4-1의 "기존 정수장" 부분.

## 1. 물리·제어 계층이란

### 1-1. 정의

- **물리 계층**: 물의 상태가 변하는 곳이다. 착수정, 약품이 섞이는 곳, 침전지와 거기 달린 센서, 펌프, 밸브. 실제 물은 없으므로 물의 상태를 계산하는 프로그램 하나(공정 프로그램)가 이 역할을 한다.
- **제어 계층**: 센서를 읽고 정해진 규칙대로 펌프와 밸브를 움직이는 장치들이다. PLC 세 대와 SIS(안전계장시스템, PLC와 따로 두는 안전 장치).

두 계층은 고리 하나로 묶여 있다.

```
센서값 ──→ PLC·SIS ──→ 펌프·밸브 명령 ──→ 공정 ──→ 센서값 ──→ …
```

### 1-2. 이 계층이 하는 일과 하지 않는 일

| 하는 일 | 하지 않는 일 |
|---|---|
| 들어온 값으로 물의 다음 상태를 계산한다 | 원수 수질이 언제 어떻게 변할지 정한다 |
| 받은 설정값대로 펌프와 밸브를 움직인다 | 설정값이 적절한지 따진다 |
| 센서값과 경보를 위로 내보낸다 | 설정값을 누가 정했는지 구분한다 |

PLC는 설정값에 맞게 장치를 움직일 뿐 그 값이 적절한지는 따지지 않는다 (문서 2-1). 이 계층은 그 PLC와 공정까지다.

### 1-3. 경계: 밖에서 들어오는 값과 밖으로 나가는 값

이 계층은 아래 값이 누구에게서 오는지 모른다. 값이 들어오는 자리만 있다.

| 방향 | 값 | 자리 | 기본값 |
|---|---|---|---|
| 들어옴 | 원수 탁도 | 상태 표의 `AIT101` | 6 NTU |
| 들어옴 | 주입률 설정값 | PLC2 서버의 `DOSE_SETPOINT` | 17.0 mg/L |
| 들어옴 | SIS 복귀 요청 | SIS 서버의 `SIS_RESET_REQUEST` | 0 |
| 나감 | 센서값, 펌프·밸브 상태, 경보 | 각 장치의 서버 | |

기본값만으로 계층 전체가 혼자 돈다. 원수 변화, 사람의 판단, 모델, 게이트웨이, 모드 전환, 주입률 한계는 이 문서에서 다루지 않는다. 그것들은 위 표의 "들어옴" 자리에 값을 넣는 쪽의 일이다.

### 1-4. 구성 요소

| 요소 | 계층 | 맡은 일 |
|---|---|---|
| 공정 프로그램 | 물리 | 수위, 주입 후 pH, 침전수 탁도 계산 |
| PLC1 | 제어 | 착수정 수위 유지 |
| PLC2 | 제어 | 약품 주입량 = 유량 × 주입률 |
| PLC3 | 제어 | 침전수 탁도 감시와 경보 |
| SIS | 제어 | pH가 범위를 벗어나면 약품 차단 |
| HMI | 창구 | 값을 화면에 띄우고 설정값을 전달 |

HMI는 감시 구역에 속하지만 값을 보고 설정값을 넣을 최소 수단이라 같이 만든다.

## 2. 구조

```
                 [HMI]
                   │ EtherNet/IP (통신 층)
   ┌───────────────┼──────────────────────────┐   가상 스위치 s1
 [PLC1]          [PLC2]          [PLC3]       [SIS]
   │               │               │            │
   └──────────── SQLite 상태 표 (물리 층) ─────────┘
                         │
                  [공정 프로그램]
```

MiniCPS에서 값이 오가는 길은 두 가지다.

- **물리 층 (SQLite 표)**: 센서와 구동기(펌프, 밸브)가 장치에 전선으로 연결된 것을 흉내 낸다. 공정 프로그램이 센서값을 쓰면 PLC가 읽고, PLC가 펌프 명령을 쓰면 공정 프로그램이 읽는다.
- **통신 층 (EtherNet/IP)**: 장치끼리 네트워크로 주고받는다. 장치마다 서버가 하나 있고, 다른 장치가 거기서 읽거나 쓴다.

통신 경로는 아래가 전부다.

| 누가 → 누구의 서버 | 값 |
|---|---|
| PLC2가 PLC1에서 읽음 | 생산 유량 |
| HMI가 넷 모두에서 읽음 | 화면에 띄울 값 |
| HMI가 PLC2에 씀 | 주입률 설정값 |
| HMI가 SIS에 씀 | 복귀 요청 |

SIS의 제어 루프는 다른 PLC의 서버에서 값을 읽지 않는다. 자기 pH 센서를 읽고 차단밸브를 직접 닫는다. SIS가 통신으로 받는 것은 HMI가 자기 서버에 써 준 복귀 요청뿐이다.

## 3. 태그

### 3-1. 물리 층

장치 번호: 1=PLC1, 2=PLC2, 3=PLC3, 4=SIS, 0=공통.

| 태그 | 번호 | 뜻 | 단위 | 처음 값 | 쓰는 쪽 → 읽는 쪽 |
|---|---|---|---|---|---|
| `SIM_TIME` | 0 | 공정 시각 | 초 | 0 | 공정 → 전부 |
| `AIT101` | 1 | 원수 탁도 | NTU | 6 | 밖 → 공정, PLC1 |
| `LIT101` | 1 | 착수정 수위 | m | 3.5 | 공정 → PLC1 |
| `FIT102` | 1 | 생산 유량 | m³/h | 2000 | 공정 → PLC1 |
| `P101` | 1 | 취수 펌프 (1 약, 2 강) | - | 2 | PLC1 → 공정 |
| `P201` | 2 | 약품 펌프 주입량 명령 | kg/h | 34 | PLC2 → 공정 |
| `AIT301` | 3 | 침전수 탁도 | NTU | 0.49 | 공정 → PLC3 |
| `AIT202` | 4 | 주입 후 pH | - | 7.145 | 공정 → SIS |
| `XV201` | 4 | 약품 차단밸브 (1 열림, 0 닫힘) | - | 1 | SIS → 공정 |

`AIT101`은 공정 프로그램이 만들지 않는다. 표에 들어 있는 값을 읽어 쓸 뿐이다. 밖에서 이 값을 바꾸면 원수가 바뀐 것이다.

### 3-2. 통신 층

| 장치 (주소) | 장치가 올리는 값 | 밖에서 써 주는 값 |
|---|---|---|
| PLC1 `192.168.1.10` | `LIT101`, `FIT102`, `AIT101`, `P101`, `SIM_TIME` | 없음 |
| PLC2 `192.168.1.20` | `P201` | `DOSE_SETPOINT` |
| PLC3 `192.168.1.30` | `AIT301`, `TURBIDITY_OVER_MINUTES`, `TURBIDITY_ALARM` | 없음 |
| SIS `192.168.1.40` | `AIT202`, `XV201` | `SIS_RESET_REQUEST` |
| HMI `192.168.1.50` | 서버 없음 | |

자료형은 두 가지만 쓴다. `P101`, `TURBIDITY_ALARM`, `XV201`, `SIS_RESET_REQUEST`는 `INT`(정수), 나머지는 전부 `REAL`(실수). 서버 태그의 번호는 그 서버를 가진 장치의 번호다 (PLC1의 `LIT101`은 `LIT101:1`, SIS의 `XV201`은 `XV201:4`).

## 4. 물리 계층: 공정 모델

### 4-1. 입력과 출력

| 받는 값 | 내는 값 |
|---|---|
| `AIT101` 원수 탁도 | `LIT101` 수위 |
| `P101` 취수 펌프 명령 | `FIT102` 생산 유량 |
| `P201` 약품 펌프 명령 | `AIT202` 주입 후 pH |
| `XV201` 차단밸브 상태 | `AIT301` 침전수 탁도 |
| | `SIM_TIME` 공정 시각 |

### 4-2. 한 걸음의 계산

공정 프로그램은 한 걸음에 공정 30초를 전진하고 아래를 순서대로 계산한다.

```
0. 읽기: AIT101, P101, P201, XV201 을 상태 표에서 읽는다

1. 수위
   취수 유량 = P101이 2(강)면 2,400, 그 외에는 1,600 m³/h
   수위 += (취수 유량 − 2,000) ÷ 200 m² × (30 ÷ 3600) 시간
   수위는 0~5 m 사이로 자른다
   생산 유량 = 2,000 m³/h (고정)

2. 약품과 pH
   약품 유량 = XV201이 0(닫힘)이면 0, 아니면 P201 을 0~250 kg/h로 자른 값
   실제 주입률 = 약품 유량 × 1000 ÷ 2,000          (mg/L)
   주입 후 pH  = 7.4 − 0.015 × 실제 주입률

3. 필요 주입률 (공정만 아는 값)
   필요 주입률 = 9.8 + 3.0 × ln(AIT101)      AIT101이 0.1보다 작으면 0.1로 본다

4. 침전수 탁도 (5시간 지연)
   차이 = 실제 주입률 − 필요 주입률 을 줄의 뒤에 넣고, 줄의 맨 앞(5시간 전의 차이)을 꺼낸다
   꺼낸 차이가 음수면: 침전수 탁도 = 0.4 + 0.15 × 차이²
   꺼낸 차이가 0 이상이면: 침전수 탁도 = 0.4 + 0.05 × 차이

5. 공정 시각 += 30
   LIT101, FIT102, AIT202, AIT301, SIM_TIME 을 상태 표에 쓰고 기록 파일에 한 줄 남긴다
```

- 줄은 600칸이다 (5시간 ÷ 30초). 처음에는 600칸 모두 기본값 기준의 차이(17 − 필요 주입률(6 NTU) ≈ +1.82)로 채운다. 매 걸음 뒤에 하나 넣고 앞에서 하나 꺼내므로 길이는 늘 600이다.
- 계산은 반올림하지 않은 값으로 한다. 상태 표와 기록 파일에 쓸 때만 소수 4자리로 반올림한다.
- 250 kg/h는 약품 펌프가 낼 수 있는 최대량이다.
- 필요 주입률은 상태 표에 쓰지 않고 기록 파일에만 남긴다. 센서로는 알 수 없는 값이기 때문이다.

### 4-3. 수식이 내는 값

구현이 맞는지 볼 때 쓰는 표다.

원수 탁도 → 필요 주입률:

| 원수 탁도 (NTU) | 6 | 20 | 100 | 300 |
|---|---|---|---|---|
| 필요 주입률 (mg/L) | 15.18 | 18.79 | 23.62 | 26.91 |

주입률 → pH (약품 유량은 주입률 × 2):

| 실제 주입률 (mg/L) | 0 | 17 | 27 | 50 | 110 | 125 |
|---|---|---|---|---|---|---|
| 약품 유량 (kg/h) | 0 | 34 | 54 | 100 | 220 | 250 |
| 주입 후 pH | 7.400 | 7.145 | 6.995 | 6.650 | 5.750 | 5.525 |

5시간 전의 차이 → 침전수 탁도:

| 차이 (mg/L) | −10 | −5 | −2 | 0 | +2 | +10 |
|---|---|---|---|---|---|---|
| 침전수 탁도 (NTU) | 15.40 | 4.15 | 1.00 | 0.40 | 0.50 | 0.90 |

모자라면 가파르게, 넘치면 완만하게 오른다 (문서 4-6의 곡선). 문서의 수치(평상시 필요량 15.38 mg/L, 침전 지연 4~7시간)에 가깝게 잡은 합성 모델이다.

## 5. 제어 계층

모든 장치는 한 주기의 일을 마친 뒤 실제 1초를 쉬고 다음 주기를 시작한다.

### 5-1. PLC1 착수정 수위

| 받는 값 | 내는 값 |
|---|---|
| `LIT101` 수위 | `P101` 취수 펌프 명령 |

```
매 주기:
    수위 = get(LIT101)
    수위 ≤ 3.3 m → P101 = 2 (강)
    수위 ≥ 3.7 m → P101 = 1 (약)
    그 사이면 직전 값 그대로 (처음 값은 2)
    P101 을 물리 층에 쓴다
    LIT101, FIT102, AIT101, P101, SIM_TIME 을 자기 서버에 올린다   (통신 1회)
```

수위는 3.3~3.7 m 근처를 약 24분(공정 시각) 주기로 오르내린다. PLC가 1초(공정 1분)에 한 번 보므로 경계를 0.03 m쯤 넘었다가 돌아온다. SWaT 예제의 PLC1과 같은 방식이다.

### 5-2. PLC2 약품 주입

| 받는 값 | 내는 값 |
|---|---|
| `FIT102` 생산 유량 (PLC1 서버에서) | `P201` 약품 펌프 명령 |
| `DOSE_SETPOINT` 주입률 설정값 (자기 서버에서) | |

```
매 주기:
    주입률 = 자기 서버의 DOSE_SETPOINT      (통신 1회)
    유량   = PLC1 서버의 FIT102             (통신 1회)
    P201 = 유량 × 주입률 ÷ 1000 을 물리 층에 쓴다
    P201 을 자기 서버에 올린다               (통신 1회)
```

예: 2,000 m³/h × 17 mg/L ÷ 1000 = 34 kg/h. 문서 2-2의 유량 비례 제어다. 받은 설정값을 검사하지 않고 그대로 쓴다.

### 5-3. PLC3 침전수 탁도

| 받는 값 | 내는 값 |
|---|---|
| `AIT301` 침전수 탁도 | `TURBIDITY_ALARM` 경보 |

```
시작할 때: 직전 시각 = get(SIM_TIME), 초과 시간 = 0
매 주기:
    탁도 = get(AIT301),  지금 = get(SIM_TIME)
    흐른 분 = (지금 − 직전 시각) ÷ 60,  직전 시각 = 지금
    탁도 > 1.0 → 초과 시간 += 흐른 분
    탁도 ≤ 1.0 → 초과 시간 = 0
    경보 = 초과 시간 ≥ 30 이면 1, 아니면 0
    AIT301, TURBIDITY_OVER_MINUTES, TURBIDITY_ALARM 을 자기 서버에 올린다   (통신 1회)
```

### 5-4. SIS

| 받는 값 | 내는 값 |
|---|---|
| `AIT202` 주입 후 pH | `XV201` 차단밸브 |
| `SIS_RESET_REQUEST` 복귀 요청 (자기 서버에서) | |

```
시작할 때: 밸브 = 1, 연속 횟수 = 0
매 주기:
    pH = get(AIT202)
    pH < 5.8 또는 pH > 8.5 → 연속 횟수 += 1,  아니면 연속 횟수 = 0
    연속 횟수 ≥ 2 이고 밸브 = 1 → 밸브 = 0

    요청 = 자기 서버의 SIS_RESET_REQUEST          (통신 1회)
    요청 = 1 이고 밸브 = 0 이고 5.8 ≤ pH ≤ 8.5 → 밸브 = 1, 연속 횟수 = 0

    set(XV201, 밸브)                                 ← 바뀔 때만이 아니라 매 주기
    요청 = 1 이면: AIT202, XV201, SIS_RESET_REQUEST = 0 을 자기 서버에 올린다   (통신 1회)
    요청 = 0 이면: AIT202, XV201 을 자기 서버에 올린다                         (통신 1회)
```

- 2번 연속은 센서값이 한 번 튄 것으로 닫지 않게 하려는 것이다.
- 밸브를 매 주기 쓰는 이유: 바뀔 때 한 번만 쓰면, 그 쓰기가 SQLite 잠금 등으로 실패했을 때 SIS는 "닫힘"인데 상태 표는 "열림"인 채로 남는다 (2026-11-19 검토에서 지적).
- 한 주기의 판정은 `cycle()` 메서드에 둔다. `tests/verify_control_logic.py`가 통신 없이 pH를 순서대로 넣어 시험한다. PLC3도 같다.
- 복귀 요청은 받아들였든 아니든 0으로 지운다.
- 밸브가 닫히면 약품이 0이 되어 pH가 7.4로 돌아온다. 그래도 밸브는 복귀 요청이 올 때까지 닫혀 있다.
- PLC2는 밸브가 닫힌 것을 모르고 계속 명령을 낸다. 공정 쪽에서 밸브가 막는다.

### 5-5. 시간

| 항목 | 값 |
|---|---|
| 가속 배율 | 60배 (실제 1초 = 공정 1분) |
| 공정 한 걸음 | 공정 30초. 걸음 사이에 실제 30 ÷ 배율 = 0.5초를 쉰다 |
| PLC·SIS 주기 | 일을 마친 뒤 실제 1초를 쉰다 |
| HMI 갱신 | 일(읽기 4회)을 마친 뒤 실제 2초를 쉰다 |
| 공정 하루 | 실제 약 24분 (60배 기준. 30배면 48분) |
| 침전 지연 5시간 | 실제 약 5분 (60배 기준. 30배면 10분) |

- 배율은 `config.py`의 상수 하나이고, 공정의 쉬는 시간은 거기서 계산한다.
- 계산과 통신에 걸리는 시간만큼 실제 주기는 조금 더 길다. 그래서 실제 배율은 60배보다 조금 낮다. 공정 시각의 기준은 `SIM_TIME` 하나다.
- PLC3의 30분은 주기 횟수가 아니라 `SIM_TIME`의 차이로 센다. PLC 주기가 느려져도 뜻이 같다.
- 배율을 정하는 기준은 하나다. 전체를 띄운 뒤 `logs/plc2.log`에 찍히는 PLC2 한 주기의 실제 시간(통신 3회 + 쉬는 1초)을 본다. 평균이 1.5초를 넘거나, 2초를 넘는 주기가 전체의 1% 이상이면 `ACCELERATION`을 30으로 낮춘다. 2초면 공정 2분이라 PLC가 공정을 너무 드물게 보기 때문이다.
- 한 번 튄 값으로는 판단하지 않는다. 2026-11-19 실행에서 PLC2·PLC3·SIS가 같은 순간에 한 번씩 1.6~2.4초로 늘어났는데(345번 중 1번), 세 장치가 함께 멈춘 것이라 컴퓨터 쪽이 잠깐 멈춘 것으로 본다. 처음 기준("2초를 넘으면")은 이 실행을 본 뒤에 고쳤다.

## 6. HMI

넷의 서버에서 값을 읽어(통신 4회) 글자 화면을 만들고 `logs/hmi_screen.txt`에 덮어쓴 뒤 2초를 쉰다. 이것을 되풀이한다. 화면은 다른 터미널에서 `watch -n 1 cat logs/hmi_screen.txt`로 본다. 화면을 파일로 내는 이유는 Mininet 명령창을 명령 입력용으로 비워 두기 위해서다.

```
정수장 HMI                                  공정 시각 1일차 14:30

착수정
  수위           3.48 m
  취수 펌프      강
  생산 유량      2000 m³/h
원수
  탁도           6.0 NTU
약품 주입
  주입률 설정    17.0 mg/L
  주입량         34.0 kg/h
  주입 후 pH     7.14  (허용 5.8~8.5)
  차단밸브       열림
침전수
  탁도           0.49 NTU  (목표 1.0 이하, 초과 0분)
경보
  없음
```

숫자는 `f"{값:.2f}"` 같은 형식으로 찍는다. 통신의 실수는 32비트라 7.145가 7.14499998…로 오고, 그래서 화면에는 7.14로 보인다. 초과 시간은 `int(값)`으로 소수를 버린다.

| 항목 | 태그 | 읽는 곳 | 표시 |
|---|---|---|---|
| 공정 시각 | `SIM_TIME` | PLC1 | `N일차 HH:MM`. 일차 = 초 ÷ 86400의 몫 + 1 |
| 수위 | `LIT101` | PLC1 | 소수 2자리 |
| 취수 펌프 | `P101` | PLC1 | 2면 "강", 그 외 "약" |
| 생산 유량 | `FIT102` | PLC1 | 정수 |
| 원수 탁도 | `AIT101` | PLC1 | 소수 1자리 |
| 주입률 설정 | `DOSE_SETPOINT` | PLC2 | 소수 1자리 |
| 주입량 | `P201` | PLC2 | 소수 1자리 |
| 주입 후 pH | `AIT202` | SIS | 소수 2자리 |
| 차단밸브 | `XV201` | SIS | 1이면 "열림", 0이면 "닫힘" |
| 침전수 탁도 | `AIT301` | PLC3 | 소수 2자리 |
| 초과 시간 | `TURBIDITY_OVER_MINUTES` | PLC3 | 정수 분 |

경보 줄: `TURBIDITY_ALARM = 1`이면 "침전수 탁도 30분 넘게 초과", `XV201 = 0`이면 "SIS 작동: 약품 차단밸브 닫힘". 둘 다면 두 줄, 없으면 "없음".

명령 (Mininet 명령창에서 `hmi` 호스트로 실행):

```
mininet> hmi venv/bin/python hmi.py dose 20        주입률 설정값 20.0 을 PLC2의 DOSE_SETPOINT 에 쓴다 (숫자가 아니거나 nan·inf면 사용법만 찍음)
mininet> hmi venv/bin/python hmi.py sis reset      SIS의 SIS_RESET_REQUEST 에 1 을 쓴다
```

인자 없이 실행하면 화면 갱신 루프를 돈다. `run.py`가 이 루프를 뒤에서 띄워 둔다.

## 7. MiniCPS 사용 규칙

원본 코드(`minicps/devices.py`, `states.py`, `protocols.py`, `examples/swat-s1/*`)에서 확인한 것이다. 구현은 이 규칙을 따른다.

| 일 | 쓰는 법 |
|---|---|
| 장치 만들기 | `PLC`, `HMI`, `Tank`를 상속하고 `pre_loop`, `main_loop`을 덮어쓴다. 객체를 만들면 바로 `pre_loop` → `main_loop`이 돈다. `HMI`는 `main_loop`만 돈다 |
| 물리 층 읽기 | `self.get(('LIT101', 1))` → 문자열. `float()`로 바꾼다 |
| 물리 층 쓰기 | `self.set(('P201', 2), 34.0)`. 표에 그 줄이 이미 있어야 한다 (UPDATE 문) |
| 통신 읽기 | `self.receive(('FIT102', 1), '192.168.1.10')` → 문자열 |
| 통신 쓰기 | `self.send(('P201', 2), 34.0, '192.168.1.20')`. 자기 서버에도 자기 주소로 쓴다 |
| 여러 개 읽기 | `self.receive_multiple([태그, 태그…], 주소)` → bytes 목록 (준 순서대로). 예: `[b'17.0', b'1', b'34.0']`. `float(b'17.0')`은 17.0이 되므로 `to_number`에 그대로 넣는다 |
| 여러 개 쓰기 | `self.send_multiple([태그, 태그…], [값, 값…], 주소)` |

지켜야 하는 것:

1. **자료형을 맞춰 보낸다.** `REAL` 태그에는 `float(값)`, `INT` 태그에는 `int(값)`을 넘긴다. 통신 클라이언트는 글자에 소수점이 있으면 실수로, 없으면 정수로 보낸다. (cpppo 5.2.5에서는 `REAL` 태그에 `17`을 보내도 17.0으로 들어가는 것을 확인했지만, 규칙은 그대로 지킨다.) 실수는 보내기 전에 `round(값, 4)`로 반올림한다. 아주 작은 수는 `1e-05`처럼 소수점 없이 찍혀 정수로 오인되고, 그러면 클라이언트가 `ValueError`를 내고 값이 바뀌지 않는다 (확인함).
2. **서버 태그는 켜질 때 전부 0이다.** 서버가 뜨는 데도 시간이 걸리고, 뜨기 전에 한 쓰기는 사라진다. 각 장치는 `pre_loop`에서 "처음 값을 쓴다 → 확인 태그를 읽는다 → 쓴 값과 같으면 끝, 다르면 0.5초 쉬고 처음부터"를 최대 20번 되풀이한다. 매번 쓰기부터 다시 한다. 20번 안에 안 되면 오류 문구를 찍고 프로그램을 끝낸다.
3. **읽은 값은 `to_number()`나 `to_numbers()`를 거친다** (8-1절 `config.py`). 통신이 실패해도 예외가 나지 않고 엉뚱한 값이 돌아오기 때문이다. 2026-11-18에 확인한 실패 모습:

   | 상황 | `receive` | `receive_multiple` |
   |---|---|---|
   | 상대 서버가 꺼져 있음 | `"b'"` (0.06초) | `[]` (0.05초) |
   | 서버에 없는 태그가 섞임 | - | 앞의 것만 돌려줌. 3개를 물었는데 `[b'0.0']` |

   `receive` 결과는 `to_number(값, 직전 값)`에 넣는다. `receive_multiple` 결과는 `to_numbers(목록, 직전 값 목록)`에 넣는다. 목록 길이가 물은 개수와 다르면 전부 직전 값을 쓴다. 길이를 보지 않고 `값[0]`처럼 꺼내면 첫째 경우에는 `IndexError`로 프로그램이 멈추고, 둘째 경우에는 값이 다른 칸에 들어간다.
4. `send`, `receive`는 부를 때마다 프로세스를 새로 띄워서 느리다. 5절에 적은 횟수보다 더 부르지 않는다.
5. 통신 층은 리눅스에서만 돈다. 실행 폴더에 `logs/`가 있어야 한다.
6. 통신 설정 사전은 키가 정확히 `name`, `mode`, `server` 셋이어야 한다. 서버가 없는 HMI는 `{'name': 'enip', 'mode': 0, 'server': ''}`.
7. 상태 설정 사전은 키가 정확히 `name`(표 이름), `path`(파일 경로, `.sqlite`로 끝남) 둘이어야 한다.

## 8. 파일별 구현 명세

```
차인프/
├─ 정수장_도메인_분석_문서.html
├─ outputs/                이 문서
└─ water_cps/
   ├─ config.py            상수, 태그, 주소, 상태 표 구조
   ├─ init.py              상태 표 만들기
   ├─ physical_process.py  공정
   ├─ plc1.py  plc2.py  plc3.py  sis.py
   ├─ hmi.py               화면 갱신과 명령
   ├─ run.py               Mininet 구성, 장치 실행
   └─ tests/
      ├─ verify_physical_process.py  공정만 1,500걸음 대본 검증 (관리자 권한 없이, 몇 초)
      ├─ verify_control_logic.py     SIS·PLC3 한 주기 판정에 센서값을 순서대로 넣어 봄 (관리자 권한 없이, 1초)
      └─ verify_system.py            전체를 띄우고 확인 1~6 자동 실행 (sudo, 약 9분)
```

SWaT 예제와 같은 구성이다 (`utils.py` → `config.py`, `topo.py`는 `run.py`에 합침).

### 8-1. `config.py`

`minicps`와 `mininet`을 import하지 않는다. `run.py`(시스템 Python)와 장치 스크립트(가상환경 Python)가 둘 다 불러 쓰기 때문이다.

아래는 구현 전에 적은 명세다. 구현(`water_cps/config.py`)은 같은 값을 쓰되 몇 가지가 달라졌다: 처음 값을 `INITIAL_*` 상수로 모으고 상태 표의 처음 값 SQL(`SCHEMA_INIT`)을 그 상수로 만든다, `write_initial_values()`가 들어 있다, `to_number()`가 nan·inf도 직전 값으로 바꾼다. 차이가 있으면 코드가 기준이다.

```python
"""정수장 약품 주입 구간: 상수, 태그, 주소."""

import math

# 시간
ACCELERATION = 60                    # 실제 1초 = 공정 60초
PROCESS_STEP_SECONDS = 30            # 공정 한 걸음 (공정 시각)
PROCESS_SLEEP_SECONDS = PROCESS_STEP_SECONDS / ACCELERATION   # 걸음 사이 실제 쉬는 시간
DEVICE_SLEEP_SECONDS = 1.0           # PLC, SIS
HMI_SLEEP_SECONDS = 2.0

# 공정
PRODUCTION_FLOW = 2000.0             # m3/h
INTAKE_FLOW_HIGH = 2400.0            # m3/h, P101 = 2
INTAKE_FLOW_LOW = 1600.0             # m3/h, P101 = 1
TANK_AREA = 200.0                    # m2
TANK_HEIGHT = 5.0                    # m
RAW_PH = 7.4
PH_DROP_PER_DOSE = 0.015             # mg/L 당 pH 하락
CHEMICAL_PUMP_MAX = 250.0            # kg/h
REQUIRED_DOSE_BASE = 9.8             # mg/L
REQUIRED_DOSE_SLOPE = 3.0            # mg/L per ln(NTU)
MINIMUM_RAW_TURBIDITY = 0.1          # NTU, ln 계산용 하한
SETTLING_DELAY_SECONDS = 5 * 3600    # 공정 시각
SETTLED_TURBIDITY_BASE = 0.4         # NTU
UNDERDOSE_COEFFICIENT = 0.15
OVERDOSE_COEFFICIENT = 0.05

# 제어
LEVEL_LOW = 3.3                      # m, 이하면 펌프 강
LEVEL_HIGH = 3.7                     # m, 이상이면 펌프 약
TURBIDITY_LIMIT = 1.0                # NTU
TURBIDITY_ALARM_MINUTES = 30
PH_LOW = 5.8
PH_HIGH = 8.5
SIS_TRIP_COUNT = 2

# 처음 값
INITIAL_RAW_TURBIDITY = 6.0
INITIAL_LEVEL = 3.5
INITIAL_DOSE_SETPOINT = 17.0

# 네트워크
ADDRESS = {
    'plc1': '192.168.1.10',
    'plc2': '192.168.1.20',
    'plc3': '192.168.1.30',
    'sis':  '192.168.1.40',
    'hmi':  '192.168.1.50',
}
NETMASK = '/24'

# 물리 층 태그 (이름, 장치 번호)
SIM_TIME = ('SIM_TIME', 0)
AIT101 = ('AIT101', 1)
LIT101 = ('LIT101', 1)
FIT102 = ('FIT102', 1)
P101 = ('P101', 1)
P201 = ('P201', 2)
AIT301 = ('AIT301', 3)
AIT202 = ('AIT202', 4)
XV201 = ('XV201', 4)

# 통신 층 태그 (이름, 서버를 가진 장치 번호). 물리 층과 이름·번호가 같은 것은 그대로 쓴다
SIM_TIME_PLC1 = ('SIM_TIME', 1)
DOSE_SETPOINT = ('DOSE_SETPOINT', 2)
TURBIDITY_OVER_MINUTES = ('TURBIDITY_OVER_MINUTES', 3)
TURBIDITY_ALARM = ('TURBIDITY_ALARM', 3)
SIS_RESET_REQUEST = ('SIS_RESET_REQUEST', 4)

# 서버 선언 (이름, 번호, 자료형)
PLC1_TAGS = (('LIT101', 1, 'REAL'), ('FIT102', 1, 'REAL'), ('AIT101', 1, 'REAL'),
             ('P101', 1, 'INT'), ('SIM_TIME', 1, 'REAL'))
PLC2_TAGS = (('P201', 2, 'REAL'), ('DOSE_SETPOINT', 2, 'REAL'))
PLC3_TAGS = (('AIT301', 3, 'REAL'), ('TURBIDITY_OVER_MINUTES', 3, 'REAL'),
             ('TURBIDITY_ALARM', 3, 'INT'))
SIS_TAGS = (('AIT202', 4, 'REAL'), ('XV201', 4, 'INT'), ('SIS_RESET_REQUEST', 4, 'INT'))


def server_protocol(device_name, tags):
    """서버가 있는 장치의 통신 설정."""
    return {'name': 'enip', 'mode': 1,
            'server': {'address': ADDRESS[device_name], 'tags': tags}}


CLIENT_PROTOCOL = {'name': 'enip', 'mode': 0, 'server': ''}

# 상태 표
STATE = {'name': 'water_cps', 'path': 'water_cps.sqlite'}
SCHEMA = """
CREATE TABLE water_cps (
    name  TEXT NOT NULL,
    pid   INTEGER NOT NULL,
    value TEXT,
    PRIMARY KEY (name, pid)
);
"""
SCHEMA_INIT = """
INSERT INTO water_cps VALUES ('SIM_TIME', 0, '0');
INSERT INTO water_cps VALUES ('AIT101',   1, '6.0');
INSERT INTO water_cps VALUES ('LIT101',   1, '3.5');
INSERT INTO water_cps VALUES ('FIT102',   1, '2000.0');
INSERT INTO water_cps VALUES ('P101',     1, '2');
INSERT INTO water_cps VALUES ('P201',     2, '34.0');
INSERT INTO water_cps VALUES ('AIT301',   3, '0.49');
INSERT INTO water_cps VALUES ('AIT202',   4, '7.145');
INSERT INTO water_cps VALUES ('XV201',    4, '1');
"""

PROCESS_LOG = 'logs/process.csv'
HMI_SCREEN = 'logs/hmi_screen.txt'


def to_number(text, previous):
    """읽은 글자를 실수로 바꾼다. 못 바꾸거나 nan·inf면 직전 값을 돌려준다."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return previous
    return value if math.isfinite(value) else previous


def to_numbers(texts, previous_values):
    """receive_multiple 결과를 실수 목록으로 바꾼다.
    길이가 물은 개수와 다르면 칸이 어긋났을 수 있으므로 전부 직전 값을 쓴다."""
    if not isinstance(texts, list) or len(texts) != len(previous_values):
        return list(previous_values)
    return [to_number(text, previous) for text, previous in zip(texts, previous_values)]


assert to_number("b'", 2000.0) == 2000.0
assert to_number('nan', 6.0) == 6.0 and to_number('inf', 6.0) == 6.0
assert to_numbers([], [3.5, 2.0]) == [3.5, 2.0]
assert to_numbers([b'0.0'], [3.5, 2.0, 6.0]) == [3.5, 2.0, 6.0]
assert to_numbers([b'3.4', b'1'], [3.5, 2.0]) == [3.4, 1.0]
```

### 8-2. `init.py`

`water_cps.sqlite`가 있으면 지우고, `sqlite3`로 `SCHEMA`와 `SCHEMA_INIT`을 실행한다. `logs/` 폴더가 없으면 만든다. `create_state()` 함수 하나로 두고 `python3 init.py`로도 실행되게 한다. Python 표준 라이브러리와 `config`만 import한다 (`minicps`를 쓰지 않는다).

### 8-3. `physical_process.py`

- 수식 함수 셋을 파일 위쪽에 둔다.
  - `required_dose(raw_turbidity)` → mg/L
  - `dosed_ph(actual_dose)` → pH
  - `settled_turbidity(dose_difference)` → NTU
- `class WaterProcess(Tank)`. 생성: `WaterProcess(name='process', protocol=None, state=STATE, section=TANK_AREA, level=INITIAL_LEVEL)`.
- `pre_loop`: 수식 점검(4-3절 표에서 `required_dose(6)≈15.18`, `dosed_ph(110)≈5.75`, `settled_turbidity(-2)≈1.0`, `settled_turbidity(2)≈0.5`를 `assert`로 확인, 오차 0.01). 수위 = `INITIAL_LEVEL`, 공정 시각 = 0. 줄 = `collections.deque`에 `INITIAL_DOSE_SETPOINT − required_dose(INITIAL_RAW_TURBIDITY)`를 600개 (`maxlen`은 주지 않는다. 걸음마다 `append` 뒤 `popleft`). 기록 파일을 새로 만들고 머리줄을 쓴다.
- `main_loop`: 끝없이 4-2절의 0~5를 하고 `time.sleep(PROCESS_SLEEP_SECONDS)`.
  - 읽기: `to_number(self.get(태그), 직전 값)`. `P101`과 `XV201`은 `int()`로 바꾼다.
  - 쓰기: `self.set(태그, 값)`. 실수는 `round(값, 4)`. 수위와 줄 같은 내부 값은 반올림하지 않은 채로 들고 있는다.
- 기록 파일 `logs/process.csv` (걸음마다 한 줄 덧붙임, `csv` 모듈, 실수는 소수 4자리). 열:
  `sim_time_seconds, raw_turbidity_ntu, level_m, intake_pump, chemical_pump_command_kg_per_hour, shutoff_valve, actual_dose_mg_per_liter, required_dose_mg_per_liter, dosed_ph, settled_turbidity_ntu`

### 8-4. `plc1.py`, `plc2.py`, `plc3.py`, `sis.py`

넷 다 같은 틀이다.

```python
class Plc2(PLC):
    def pre_loop(self):
        # 자기 서버에 처음 값을 쓰고 읽어 확인 (7절 규칙 2)
    def main_loop(self):
        while True:
            # 5절의 한 주기
            time.sleep(DEVICE_SLEEP_SECONDS)

if __name__ == '__main__':
    Plc2(name='plc2', state=STATE, protocol=server_protocol('plc2', PLC2_TAGS))
```

| 파일 | 클래스 | `pre_loop`에서 자기 서버에 쓰는 처음 값 | 확인에 쓰는 태그 |
|---|---|---|---|
| `plc1.py` | `Plc1` | `LIT101`=3.5, `FIT102`=2000.0, `AIT101`=6.0, `P101`=2, `SIM_TIME_PLC1`=0.0 | `FIT102` |
| `plc2.py` | `Plc2` | `P201`=34.0, `DOSE_SETPOINT`=17.0 | `DOSE_SETPOINT` |
| `plc3.py` | `Plc3` | `AIT301`=0.49, `TURBIDITY_OVER_MINUTES`=0.0, `TURBIDITY_ALARM`=0 | `AIT301` |
| `sis.py` | `Sis` | `AIT202`=7.145, `XV201`=1, `SIS_RESET_REQUEST`=0 | `XV201` |

- `pre_loop`의 한 번 시도는 "`send_multiple`로 처음 값 전부 쓰기 → 확인 태그를 `receive`로 읽기 → `to_number(읽은 값, None)`이 쓴 값과 0.01 이내면 통과"다. 통과하지 못하면 0.5초 쉬고 쓰기부터 다시 한다 (7절 규칙 2). 네 장치가 똑같이 쓰므로 `config.py`의 `write_initial_values(장치, 주소, 태그 목록, 값 목록, 확인 태그)` 하나로 둔다. 장치 객체의 `send_multiple`, `receive`를 부르기만 하므로 `config.py`는 여전히 `minicps`를 import하지 않는다.
- 첫 시도는 서버가 아직 안 떠서 `ConnectionRefusedError` Traceback이 기록에 한 번 남는 것이 보통이다. 다음 줄에 `initial values written to ...`가 있으면 정상이다.
- 주기 안의 통신은 5절에 적은 대로다. 여러 태그를 올릴 때는 `send_multiple`을 쓴다.
- 주기 끝에 그 주기에 걸린 실제 시간(쉬는 시간 포함)을 `print`로 한 줄 찍는다. 예: `cycle_seconds=1.42`. `logs/<이름>.log`에 남고, 배율을 정할 때 본다 (5-5절).
- PLC1은 물리 층의 `SIM_TIME`(번호 0)을 읽어 자기 서버의 `SIM_TIME_PLC1`(번호 1)로 올린다. 이름은 같고 번호가 다르다. 나머지 태그는 물리 층과 서버의 이름·번호가 같다.
- PLC2가 PLC1에서 읽은 유량과 자기 서버에서 읽은 설정값은 `to_number(읽은 값, 직전 값)`으로 바꾼다. 직전 값의 처음 값은 2000.0과 17.0이다.
- SIS가 읽는 복귀 요청은 `int(to_number(읽은 값, 0))`이다.
- 물리 층에 쓸 때 `P101`과 `XV201`은 정수, `P201`은 `round(값, 4)`.

### 8-5. `hmi.py`

- `class Hmi(HMI)`. 생성: `Hmi(name='hmi', state=STATE, protocol=CLIENT_PROTOCOL)`.
- `main_loop`은 `sys.argv`를 보고 갈라진다.
  - 인자 없음: 끝없이 넷의 서버에서 `receive_multiple`로 읽고(장치마다 1회), 6절의 화면 글자를 만들어 `logs/hmi_screen.txt.tmp`에 쓴 뒤 `os.replace`로 `logs/hmi_screen.txt`에 덮어쓰고 `time.sleep(HMI_SLEEP_SECONDS)`.
  - `dose 숫자`: `self.send(DOSE_SETPOINT, round(float(숫자), 4), ADDRESS['plc2'])` 하고 끝낸다. 숫자로 못 바꾸면 사용법을 찍고 끝낸다.
  - `sis reset`: `self.send(SIS_RESET_REQUEST, 1, ADDRESS['sis'])` 하고 끝낸다.
  - 그 외: 사용법을 찍고 끝낸다.
- 장치마다 읽은 목록은 `to_numbers(목록, 그 장치의 직전 값 목록)`을 거친다 (7절 규칙 3). 직전 값의 처음 값은 8-4절 표의 서버 처음 값이다 (예: `LIT101` 3.5, `P201` 34.0, `XV201` 1). 공정 시각은 PLC1 서버의 `SIM_TIME_PLC1` 태그에서 읽는다.
- 장치마다 읽는 태그와 순서:

  | 장치 | 태그 (이 순서로 묻는다) |
  |---|---|
  | PLC1 | `SIM_TIME_PLC1`, `LIT101`, `P101`, `FIT102`, `AIT101` |
  | PLC2 | `DOSE_SETPOINT`, `P201` |
  | PLC3 | `AIT301`, `TURBIDITY_OVER_MINUTES`, `TURBIDITY_ALARM` |
  | SIS | `AIT202`, `XV201` |

### 8-6. `run.py`

시스템 Python으로 관리자 권한(`sudo`)으로 실행한다. 외부 패키지는 `mininet`만 쓰고, 그 밖에는 Python 표준 라이브러리와 `config`, `init`을 import한다 (`minicps`를 쓰지 않는다).

```
0. os.chdir(run.py가 있는 폴더)
   상태 표, logs/, venv/ 를 모두 상대 경로로 쓰므로, 어디서 실행해도 그 폴더에서 돌게 한다.
   Mininet 호스트에서 띄운 장치도 이 폴더를 작업 폴더로 물려받는다.
1. 이전 실행의 찌꺼기를 정리한다:
   pkill -f "cpppo.server.enip" ; pkill -f "plc[123].py|sis.py|physical_process.py|hmi.py"
2. init.create_state()
3. 가상 네트워크: 스위치 s1 하나에 호스트 다섯 (plc1, plc2, plc3, sis, hmi), 주소는 config.ADDRESS + NETMASK
   Mininet(topo=..., switch=OVSBridge, controller=None)
   net.start(); net.pingAll()
4. 장치를 띄운다. 명령은 "<가상환경 python> -u <파일> > logs/<이름>.log 2>&1 &"
   가상환경 python = os.path.abspath('venv/bin/python')
   plc1, plc3, sis   → 셋의 기록에 "initial values written"이 찍힐 때까지 기다림
   plc2              → 같은 방법으로 기다림
   (20초 안에 안 찍히거나 "ERROR could not write initial values"가 찍히면 오류를 내고 6번으로 간다)
   physical_process (s1 에서 실행)
   hmi (hmi 호스트에서 인자 없이)
5. CLI(net)            ← 여기서 사용자가 명령을 넣는다. exit 하면 다음으로
6. 1번의 정리를 다시 하고 net.stop()
```

- 띄우는 순서를 지키는 이유: PLC2가 PLC1보다 먼저 돌면 유량을 0으로 읽는다. 공정을 마지막에 띄우는 이유: 장치가 준비되기 전에는 공정 시각이 흐르지 않게 하려는 것이다.
- 정해진 초만 쉬지 않고 준비 신호를 기다리는 이유: PLC1의 처음 값 쓰기가 늦어지면 PLC2가 유량 0.0을 정상 숫자로 읽어 약품을 0으로 명령한다. 그 물은 5시간 뒤 침전수 탁도를 약 35 NTU로 올린다 (2026-11-19 검토에서 지적).
- `OVSBridge`는 컨트롤러 없이 동작하는 스위치다. Mininet 기본 스위치는 컨트롤러 프로그램이 따로 필요하다. `Mininet(switch=OVSBridge, controller=None)` 구성은 2026-11-18에 확인했다 (9절).
- `run.py`가 오류로 중간에 끝나 가상 네트워크가 남았으면, 다시 띄우기 전에 `sudo mn -c`로 지운다.
- `sudo`로 돌리므로 `water_cps.sqlite`와 `logs/` 안의 파일 주인이 root가 된다. 그 뒤 관리자 권한 없이 확인 A를 다시 하려면 먼저 `sudo chown -R $USER ~/water_cps`를 한다. 안 하면 기록 파일을 열 때 권한 오류가 난다.

## 9. 환경 준비 (0단계)

2026-11-18에 아래 1~4번을 이 컴퓨터의 WSL에서 모두 실행해 통과했다.

| 번호 | 결과 |
|---|---|
| 1 | Mininet 2.3.0, Open vSwitch 3.7.1 설치. 서비스 `active` |
| 2 | `Results: 0% dropped (2/2 received)` |
| 3 | Python 3.12.15 가상환경, MiniCPS 1.1.4(GitHub master), cpppo 5.2.5. import 성공 |
| 4 | 같은 컴퓨터 안에서 `[17.5]`, `[1]` 확인. 통신 한 번 약 0.08초 |
| 추가 | Mininet 가상 네트워크를 건너서도(`hmi` → `plc2` 호스트) 같은 결과. 통신 한 번 평균 0.079초, 최대 0.082초. PLC2 한 주기 추정 1.24초 (`~/water_cps/mininet_enip_check.py`) |
| 추가 | 프로세스 6개가 SQLite 표 하나를 15초 동안 0.02초 간격으로 읽고 씀 (실제 설계보다 수십 배 잦음). 읽기 38,376번 중 숫자가 아닌 값 0번 |
| 추가 | 통신이 실패할 때 돌아오는 값 (7절 규칙 3의 표) |

환경: WSL2 Ubuntu 26.04, 커널 6.6.114, 시스템 Python 3.14, `uv` 0.13.1. PyPI의 `minicps`는 2017년의 Python 2용(1.1.3)이라 쓸 수 없어 GitHub의 master를 설치한다.

`sudo`가 붙은 명령은 비밀번호가 필요하므로 사용자가 직접 실행한다.

```bash
# 1. 패키지
sudo apt update
sudo apt install -y mininet openvswitch-switch sqlite3 git
sudo service openvswitch-switch start

# 2. Mininet 이 도는지
sudo mn --switch ovsbr --controller none --test pingall

# 3. 작업 폴더와 가상환경 (Windows 폴더가 아니라 리눅스 폴더에 둔다)
mkdir -p ~/water_cps && cd ~/water_cps
uv venv --python 3.12 venv
uv pip install --python venv/bin/python "cpppo==5.2.5" pymodbus pandas "git+https://github.com/scy-phy/minicps"
venv/bin/python -c "import minicps.devices; print('ok')"

# 4. 통신이 되는지 (터미널 둘)
#    터미널 A
mkdir -p logs && venv/bin/python -m cpppo.server.enip --address 127.0.0.1:44818 'DOSE_SETPOINT:2=REAL' 'XV201:4=INT'
#    터미널 B
venv/bin/python -m cpppo.server.enip.client --print --address 127.0.0.1:44818 'DOSE_SETPOINT:2=17.5' 'XV201:4=1' 'DOSE_SETPOINT:2' 'XV201:4'
```

판단 기준:

| 결과 | 다음 행동 |
|---|---|
| 2번에서 ping 손실 0% | 통과 |
| 2번 실패 | `sudo modprobe openvswitch` 뒤 다시. 그래도 안 되면 `sudo apt install bridge-utils` 하고 `--switch lxbr`로 시험. 되면 `run.py`의 스위치를 `LinuxBridge`(`mininet.nodelib`)로 바꾼다. 둘 다 안 되면 VirtualBox Ubuntu 가상머신 |
| 3번에서 `ok` | 통과 |
| 3번 실패 | `rm -rf venv` 뒤 `uv venv --python 3.11 venv`로 가상환경을 다시 만들고 3번부터 다시 |
| 4번 B가 `[17.5]`와 `[1]`을 찍음 | 밑줄이 든 태그 이름, 실수·정수 쓰기가 된다. 통과 |

cpppo 버전: MiniCPS의 `requirements.txt`는 4.3.4로 고정돼 있지만, 4.3.4는 Python 3.12에서 import 단계에서 실패한다 (`cpppo/misc.py`의 `types.CodeType` 호출, `TypeError: code() argument 13 must be str, not int`). 5.2.5는 Python 3.12에서 import, 서버, 클라이언트, MiniCPS의 `send`·`receive`·`send_multiple`·`receive_multiple`이 모두 된다 (2026-11-18 WSL에서 확인).

배율(60 또는 30)은 여기서 정하지 않는다. 전체를 구현해 띄운 뒤 5-5절의 기준으로 정한다.

`pymodbus`는 쓰지 않지만 MiniCPS가 import하므로 설치한다. `pandas`도 MiniCPS의 의존 패키지다.

Windows 폴더의 코드를 리눅스 폴더로 복사하는 명령: `cp /mnt/c/Users/user/Desktop/학교수업/차인프/water_cps/*.py ~/water_cps/ && mkdir -p ~/water_cps/tests && cp /mnt/c/Users/user/Desktop/학교수업/차인프/water_cps/tests/*.py ~/water_cps/tests/`

## 10. 구현 순서와 확인

| 단계 | 할 일 | 끝난 기준 |
|---|---|---|
| 0 | 9절 | 판단 기준 표를 다 통과한다 |
| 1 | `config.py`, `init.py`, `physical_process.py` | 확인 A |
| 2 | `plc1.py`, `plc2.py`, `plc3.py`, `sis.py`, `hmi.py`, `run.py` | 확인 1~6, 배율 확정 (5-5절) |

실행:

```bash
cd ~/water_cps
sudo python3 run.py                         # Mininet 명령창이 뜬다
watch -n 1 cat logs/hmi_screen.txt          # 다른 터미널에서 화면 보기
```

원수 탁도는 이 계층이 정하지 않는다. 바꿔 보려면 다른 터미널에서 상태 표의 값을 직접 고친다:

```bash
sudo sqlite3 ~/water_cps/water_cps.sqlite "UPDATE water_cps SET value='300' WHERE name='AIT101'"
```

확인 A (공정만, 관리자 권한 없이): `python3 init.py` 뒤 `venv/bin/python physical_process.py`를 1분 돌리고 멈춘다. `logs/process.csv`에서 `dosed_ph`가 7.145, `settled_turbidity_ntu`가 0.4912, `required_dose_mg_per_liter`가 15.1753이면 된다. `level_m`은 3.5에서 한 줄에 1/60 m(약 0.0167)씩 올라 90번째 줄에서 5.0이 되고 거기서 멈춘다 (PLC1이 없어 펌프가 계속 강이다).

확인 1~6 (전체를 띄우고): 입력을 하나 바꾸고 출력을 본다. 값은 HMI 화면이나 `logs/process.csv`에서 읽는다. 확인 1~4는 각각 `run.py`를 새로 띄워 기본값에서 시작한다. 확인 5만 확인 4에 이어서 한다. 시간은 60배 기준이다.

| 번호 | 블록 | 바꾸는 것 | 기대 |
|---|---|---|---|
| 1 | PLC1 + 공정 | 없음 | 수위가 3.25~3.75 m 사이를 오르내린다 |
| 2 | PLC2 | `hmi.py dose 20` | 주입량 34 → 40 kg/h, 기록 파일의 `dosed_ph` 7.145 → 7.10 |
| 3 | 공정 + PLC3 | 원수 탁도를 300으로 | 바꾼 때부터 공정 5시간(실제 약 5분) 뒤 침전수 탁도 15.14 NTU, 그 30분(실제 약 30초) 뒤 경보 |
| 4 | SIS | `hmi.py dose 110` | 주입량 220 kg/h, `dosed_ph` 5.75. SIS가 범위 밖 pH를 처음 읽은 주기부터 두 번째 주기에 차단밸브 닫힘, 그 뒤 `dosed_ph` 7.40 |
| 5 | SIS + HMI | 확인 4에 이어 `hmi.py dose 17`. 화면의 주입량이 34.0이 된 것을 본 뒤 `hmi.py sis reset` | 차단밸브 열림, `dosed_ph` 7.145 |
| 6 | HMI | 없음 | `logs/hmi_screen.txt`가 몇 초마다 바뀌고 6절의 모양이다 |

다시 확인하는 명령 (코드를 고친 뒤 돌린다):

```bash
cd ~/water_cps
venv/bin/python tests/verify_physical_process.py          # 공정만. 끝에 RESULT=PASS
venv/bin/python tests/verify_control_logic.py             # SIS·PLC3 판정. 끝에 RESULT=PASS
sudo python3 tests/verify_system.py | tee logs/verify_system_output.txt   # 전체. 끝에 RESULT=PASS
```

2026-11-19 재검증 (codex와 Claude 검토자의 지적 반영 뒤, 판정 14개로 늘린 `tests/verify_system.py`): 14/14 통과. 처음 값 기다리기, SIS 매 주기 쓰기, 20분에 경보 없음·30분에 경보, 복귀 요청 지우기, SIS 2걸음 이상까지 포함. PLC2 주기 평균 1.241초, 최대 1.330초, 2초 넘은 주기 0/349. 실제 배율 58.6배. 관리자 권한 없이 도는 `verify_control_logic.py`(22개 판정)와 `verify_physical_process.py`도 통과.

2026-11-18 실행 결과 (`tests/verify_system.py`로 확인 1~6을 한 번 띄운 상태에서 이어서 자동 실행, 13개 항목 전부 통과, 실제 435초):

| 항목 | 측정 |
|---|---|
| 공정을 `s1`에서 띄우기 | 기록이 쌓임 |
| 확인 1 | 수위 최소 3.2833, 최대 3.7167 m, 펌프 1·2 모두 나옴 |
| 확인 2 | 주입량 40.0 kg/h, pH 7.1 |
| 확인 3 | 바꾼 뒤 4시간 59분 30초에 0.4912, 5시간에 15.1352 NTU. 그 뒤 경보 |
| 확인 4 | pH 5.75가 공정 4걸음(실제 약 2초) 이어진 뒤 밸브 닫힘, 그 뒤 pH 7.4 |
| 설정 110인 채 복귀 | 열렸다가 다시 닫힘 |
| 확인 5 | 밸브 열림, pH 7.145 |
| 확인 6 | 화면 파일 갱신 |
| 주기 | PLC1 평균 1.09초, PLC2 평균 1.24초·최대 1.33초, PLC3 1.09초, SIS 1.16초 |
| 실제 가속 배율 | 57.9배 (공정 25,170초 / 실제 435초). 60배 유지 |

- 확인 5에서 주입량이 34.0으로 바뀌기 전에 복귀시키면, 아직 220인 명령 때문에 다시 닫힌다.
- 확인 4에서 설정을 110으로 둔 채 `sis reset`을 하면 밸브가 열렸다가 다시 닫힌다. 설계대로다.
- 밸브가 닫혀 있던 시간의 물은 공정 5시간 뒤 침전수 탁도를 올린다. 원수 탁도가 6이면 약 35 NTU다. 설계대로다.

## 11. 확인하지 못한 것과 일부러 남긴 한계

### 11-1. 확인하지 못한 것

- 통신이 계속 실패하면 장치는 직전 값으로 계속 돈다. 경보를 띄우지 않는다.
- 공정 수식은 문서의 수치에 맞춘 합성 모델이고 실제 정수장 자료로 맞춘 것이 아니다.
- 공정 약 7시간 넘게 돌려 보지 않았다. 오래 돌리면 `logs/process.csv`가 계속 커지고, 공정 약 194일부터는 PLC1이 올리는 `SIM_TIME`(32비트 실수)이 초 단위로 정확하지 않다.
- 사람이 Mininet 명령창에서 직접 `hmi` 명령을 넣는 경로는 해 보지 않았다. 검증 스크립트가 같은 명령을 HMI 호스트에서 실행했다.

### 11-2. 일부러 남긴 한계 (다른 단계에서 다룬다)

| 한계 | 지금 동작 | 막는 곳 (도메인 문서 기준) | 다룰 단계 |
|---|---|---|---|
| PLC2가 설정값을 검사하지 않음 | `dose 300`을 그대로 따른다. 과주입이면 SIS가 막는다 | 게이트웨이가 먼저 거르고, **PLC2가 마지막에 5~50 mg/L, 1분 2 mg/L로 자른다** (4-2, 4-3절) | 스마트 정수장 단계. 고칠 곳은 이 계층의 PLC2 |
| 장치 프로그램이 죽어도 아무도 모름 | 마지막 명령이 상태 표에 남아 공정이 그 값을 계속 읽는다 | 알아채는 것은 감시 층(HMI가 값이 안 바뀌는 것을 봄) | 감시 층 단계. "죽으면 펌프 정지"를 흉내 내려면 이 계층의 공정 프로그램도 고친다 |
| 통신에 인증이 없음 | 누구든 서버에 쓸 수 있다 | 출발지 주소 제한(구역·방화벽)과 PLC2의 검사 (4-7절) | 구역·방화벽 단계 + PLC2 한계 |

- 설정값 검사를 지금 넣지 않는 이유: "기존 정수장에는 없던 보호를 스마트 정수장 단계에서 추가한다"는 전후 비교를 하려는 것이다.
- 장치가 죽었을 때 위험한 쪽은 이미 막혀 있다. 마지막 명령이 과주입이면 SIS가 pH를 보고 밸브를 닫고(확인 4), 주입이 모자라면 5시간 뒤 PLC3 경보가 뜬다(확인 3). 실제 PLC는 멈추면 출력 신호가 끊겨 펌프가 서도록 배선하지만, 이 시뮬레이션은 그렇지 않다.
