"""기준값(폴백 값) 계산 방식: 수질별 주입률 표(조견표) + 하루 한 번 자-테스트 갱신.

팀 설계(중간발표 15쪽, 보안 설계 E-2): "기준값의 계산 방식은 모델 담당, 계산 위치와
전환 조건은 보안 담당". 이 파일은 계산 방식만 정한다. 어디서 돌리고 언제 이 값으로
넘어갈지는 보안 설계(게이트웨이·기준값 계산기) 쪽이 정한다.

설계가 정한 조건:
    AI 출력을 입력으로 받지 않는다. 운전 기록의 원본 센서값과 자-테스트 기록만 쓴다.
    약품은 넉넉한 쪽으로 잡는다 (모자랄 때가 더 위험하다).
    하루 한 번 자-테스트 결과로 갱신해 센서에 안 보이는 변화(조류 등)를 반영한다.

표 만드는 법: 학습 기록(dataset/train.csv)을 원수 탁도 구간으로 나누고, 구간마다 자-테스트
값의 95 백분위수에 여유 2 mg/L를 더한다. 조류 기간은 탁도와 상관없이 필요량이 달라
표에 넣지 않는다(자-테스트 갱신이 메운다).

실행:
    venv/bin/python baseline.py build          dataset/baseline_table.json 생성
    venv/bin/python baseline.py 120            탁도 120 NTU의 기준값 출력
    venv/bin/python baseline.py 6 --jar 22.5   자-테스트 22.5 mg/L를 반영한 기준값
"""

import csv
import json
import os
import sys

WATER_CPS_DIR = os.path.dirname(os.path.abspath(__file__))
TRAIN_CSV = os.path.join(WATER_CPS_DIR, 'dataset', 'train.csv')
TABLE_FILE = os.path.join(WATER_CPS_DIR, 'dataset', 'baseline_table.json')

# 탁도 구간 경계 (NTU). 마지막 구간은 위로 열려 있다
BAND_EDGES = [0, 10, 20, 50, 100, 200, 500]
PERCENTILE = 0.95
MARGIN = 2.0          # 운영자 과주입 여유와 같은 값
WARMUP_STEPS = 600    # 5시간 지연 큐가 처음 값으로 차 있는 구간


def band_index(turbidity):
    index = 0
    for i, edge in enumerate(BAND_EDGES):
        if turbidity >= edge:
            index = i
    return index


def build(csv_path=TRAIN_CSV, out_path=TABLE_FILE):
    """기록으로 조견표를 만든다. 입력은 탁도계 값과 자-테스트 값뿐이다."""
    per_band = [[] for _ in BAND_EDGES]
    with open(csv_path) as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i < WARMUP_STEPS or row['water_type'] == 'algae':
                continue
            per_band[band_index(float(row['measured_turbidity_ntu']))].append(
                float(row['jar_test_dose_mg_per_l']))
    rows = []
    previous = None
    for i, doses in enumerate(per_band):
        if doses:
            doses.sort()
            value = doses[min(len(doses) - 1, int(len(doses) * PERCENTILE))] + MARGIN
        else:
            # 기록이 없는 구간(처음 보는 탁도)은 아래 구간 값에 여유를 한 번 더 얹는다 (넉넉한 쪽)
            value = None if previous is None else previous + MARGIN
        # 탁도가 높을수록 줄지 않게 (넉넉한 쪽)
        if previous is not None and value is not None:
            value = max(value, previous)
        rows.append({'from_ntu': BAND_EDGES[i],
                     'to_ntu': BAND_EDGES[i + 1] if i + 1 < len(BAND_EDGES) else None,
                     'dose_mg_per_l': None if value is None else round(value, 2),
                     'records': len(doses)})
        previous = value
    table = {'method': f'구간별 자-테스트 {int(PERCENTILE * 100)} 백분위수 + {MARGIN} mg/L',
             'bands': rows}
    with open(out_path, 'w') as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
    return table


def load(path=TABLE_FILE):
    with open(path) as f:
        return json.load(f)


def table_dose(turbidity, table):
    return table['bands'][band_index(turbidity)]['dose_mg_per_l']


def jar_correction(jar_test_dose, turbidity_at_test, table):
    """자-테스트 갱신량. 표보다 실험값(+여유)이 높을 때만 올린다 (넉넉한 쪽)."""
    return max(0.0, jar_test_dose + MARGIN - table_dose(turbidity_at_test, table))


def baseline_dose(turbidity, table, correction=0.0):
    return table_dose(turbidity, table) + correction


def _print_table(table):
    print('기준값 조견표 (%s)' % table['method'])
    for band in table['bands']:
        upper = '%5s' % ('' if band['to_ntu'] is None else band['to_ntu'])
        print('  %5s ~ %s NTU  %6.2f mg/L  (기록 %d건)'
              % (band['from_ntu'], upper, band['dose_mg_per_l'], band['records']))


if __name__ == '__main__':
    arguments = sys.argv[1:]
    if arguments == ['build']:
        _print_table(build())
        print('저장: %s' % TABLE_FILE)
    elif arguments:
        table = load()
        turbidity = float(arguments[0])
        correction = 0.0
        if len(arguments) == 3 and arguments[1] == '--jar':
            correction = jar_correction(float(arguments[2]), turbidity, table)
        print('탁도 %.1f NTU → 기준값 %.2f mg/L (자-테스트 갱신 %+.2f)'
              % (turbidity, baseline_dose(turbidity, table, correction), correction))
    else:
        print(__doc__)
