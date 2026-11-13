"""툴 서버가 다루는 거래 모델.

정답 라벨(is_fraud), 카드 원번호, 고객 개인정보는 이 모델에 필드가 없습니다.
타입 수준에서 라벨과 개인정보가 조사 경로로 들어올 수 없게 합니다.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Transaction:
    transaction_id: str
    card_token: str
    trans_at: datetime
    amount: Decimal
    category: str
    merchant_name: str
