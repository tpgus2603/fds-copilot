# W1 합성 카드 거래

`transactions.csv`는 Sparkov 적재기의 필수 컬럼과 W3용 가맹점 좌표만 포함합니다.
`cc_num`에는 실제 PAN 대신 **SYNTHETIC-**으로 시작하는 가짜 카드 식별자를 넣었습니다.
`evaluation_labels.csv`는 일부 대상 거래의 평가용 정답이며, MCP 적재에 사용하지 않습니다.
`expected_history.json`은 사례별 호출 인자와 독립적으로 계산한 기대값입니다.
`target_category_seen`, `recent_transaction_ids`, `same_second_excluded`는 각각 응답의
`target_category.seen_before`, `recent_transactions[*].transaction_id`, `quality.notes`를 비교할 항목입니다.

| 사례 | 확인할 내용 |
|---|---|
| T1, T5~T7, T12 / `high` | 10·20·30·40·50의 평균 30, 대상 300은 10배입니다. 90일 경계의 50은 포함합니다. 다른 카드, 경계 1초 이전, 동시각, 1초 이후의 거래는 제외합니다. 7일 조회에는 4건만 남습니다. |
| T2 / `usual` | 5건 평균 25, 대상 25, 동일 업종 이용 이력이 있습니다. |
| T3 / `sparse`, T4 / `new` | 3건 평균 2인 표본 부족과 이력이 없는 경우를 분리합니다. |
| T8~T10 | `missing`은 CSV에 없고 허용 목록에 넣습니다(T8). `otherdecoy`는 존재하지만 허용 목록에 넣지 않습니다(T9). `high`에 `lookback_days: 400`을 전달합니다(T10). |
| T13 / `edge4`, `edge5`, `edge19`, `edge20` | 표본은 1부터 n까지입니다. p95는 20건에서만 19.00입니다. |
| T14 / `zero`, `tiny`, `share` | 실제 평균 0이면 비율 null, 실제 평균 0.002이면 표시 평균 0.00이어도 비율 500입니다. 별도 대용량 사례의 희소 업종 1/20,001은 0.0000으로 반올림됩니다. |
| W3 / `geo` | 10분 간격의 뉴욕·로스앤젤레스 가맹점 좌표입니다. 고객의 실측 위치를 뜻하지 않습니다. |

T11은 저장소 장애 주입, T15는 서버 시작 환경 조합으로 검증합니다. 둘 다 CSV에 오류를 넣어 표현하지 않습니다.
T12에서는 CSV에 라벨·고객 식별 정보가 없고, SQLite 및 MCP 결과에도 가짜 카드 식별자가 그대로 나오지 않는지 확인합니다.
원본의 비노출 컬럼을 의도적으로 섞는 적재 테스트는 #11의 테스트가 담당합니다.

소형 CSV와 기대값을 재생성하거나 T14의 큰 경계 사례를 임시 디렉터리에 생성할 수 있습니다.

```sh
python3 data/fixtures/generate.py
python3 data/fixtures/generate.py --include-large --out /tmp/fds-large-fixture
```

#11 적재기가 있는 체크아웃에서는 `investigator/`에서 실행합니다.

```sh
uv run --locked python -m fds_investigator.load_csv ../data/fixtures/transactions.csv /tmp/fds-fixture.sqlite
FDS_ALLOWED_TARGETS=high,usual,sparse,new,missing FDS_DB_PATH=/tmp/fds-fixture.sqlite uv run --locked fds-evidence-tools
```
