#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
仕事情報のユーティリティ関数

このモジュールは、日付処理、価格処理などの共通ユーティリティ関数を提供します。
JobMonitorAppクラスから分離された日付や価格に関する処理が含まれています。

主な機能:
- 日付の解析と変換
- 日付範囲のチェック
- 価格の抽出と変換
- 金額の範囲チェック
"""

import re
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def parse_date(date_str: str) -> Optional[datetime]:
    """
    日付文字列をdatetimeオブジェクトに変換
    
    Args:
        date_str: 変換する日付文字列
        
    Returns:
        変換されたdatetimeオブジェクト、失敗した場合はNone
    """
    if not date_str:
        return None
        
    # 複数の日付形式に対応
    date_formats = [
        '%Y/%m/%d %H:%M',  # 2023/01/01 12:34
        '%Y/%m/%d',        # 2023/01/01
        '%Y年%m月%d日 %H時%M分',  # 2023年01月01日 12時34分
        '%Y年%m月%d日',    # 2023年01月01日
        '%m/%d %H:%M',     # 01/01 12:34
    ]
    
    for date_format in date_formats:
        try:
            # 日付をパース
            dt = datetime.strptime(date_str, date_format)
            
            # 年が指定されていない場合は現在の年を設定
            if '%Y' not in date_format:
                current_year = datetime.now().year
                dt = dt.replace(year=current_year)
                
            return dt
        except ValueError:
            continue
    
    # すべての形式でパースに失敗した場合
    logger.warning(f"日付のパースに失敗しました: {date_str}")
    return None

def format_date(date_str: str) -> str:
    """
    日付文字列を整形して表示用にフォーマット
    
    Args:
        date_str: 整形する日付文字列
        
    Returns:
        整形された日付文字列
    """
    if not date_str:
        return "なし"
    
    dt = parse_date(date_str)
    if dt:
        return dt.strftime('%Y/%m/%d %H:%M')
    else:
        return "日付不明"

def get_job_date_for_sorting(job: Dict[str, Any]) -> datetime:
    """
    仕事の日付を並べ替え用に取得
    
    Args:
        job: 仕事情報
        
    Returns:
        並べ替え用の日付。取得できない場合は古い日付を返す
    """
    try:
        # 日付情報を取得
        date_str = job.get('date', '')
        
        dt = parse_date(date_str)
        if dt:
            return dt
            
        # 日付が取得できない場合は古い日付を返す
        return datetime(2000, 1, 1)
    except Exception as e:
        logger.error(f"日付の取得に失敗しました: {e}, job_id: {job.get('id', 'unknown')}")
        # エラーの場合は古い日付を返す
        return datetime(2000, 1, 1)

def is_within_days(job: Dict[str, Any], days: int) -> bool:
    """
    仕事が指定された日数以内かチェック
    
    Args:
        job: 仕事情報
        days: 日数
        
    Returns:
        指定された日数以内の場合はTrue
    """
    if days <= 0:
        return True  # 日数指定なしの場合はすべて表示
        
    try:
        date_str = job.get('date', '')
        if not date_str:
            return False
            
        job_date = parse_date(date_str)
        if not job_date:
            return False
            
        # 現在の日時と比較
        now = datetime.now()
        delta = now - job_date
        
        return delta.days <= days
    except Exception as e:
        logger.error(f"日付チェックに失敗しました: {e}, job_id: {job.get('id', 'unknown')}")
        return False  # エラーの場合は除外

def get_job_price(job: Dict[str, Any]) -> int:
    """
    仕事の価格を取得
    
    Args:
        job: 仕事情報
        
    Returns:
        価格（整数）。取得できない場合は-1
    """
    try:
        # payment情報がある場合
        payment = job.get('payment', {})
        
        # payment が文字列の場合（古いデータ形式）
        if isinstance(payment, str):
            # 数値だけを抽出
            price_match = re.search(r'(\d{1,3}(,\d{3})*)', payment)
            if price_match:
                price_str = price_match.group(1).replace(',', '')
                return int(price_str)
            return -1
            
        # payment が辞書の場合（新しいデータ形式）
        elif isinstance(payment, dict):
            # まず最低価格を確認
            min_price = payment.get('min_price')
            if min_price:
                try:
                    return int(min_price)
                except (ValueError, TypeError):
                    pass
                    
            # 次に最大価格を確認
            max_price = payment.get('max_price')
            if max_price:
                try:
                    return int(max_price)
                except (ValueError, TypeError):
                    pass
                    
            # フォールバック：payment_type を確認
            payment_type = payment.get('payment_type', '')
            if '単価' in payment_type:
                # 単価の場合は数値を抽出
                price_match = re.search(r'(\d{1,3}(,\d{3})*)', payment_type)
                if price_match:
                    price_str = price_match.group(1).replace(',', '')
                    return int(price_str)
            
            logger.warning(f"価格の取得に失敗しました: 不明な支払形式: {payment}")
            return -1
        else:
            logger.warning(f"価格の取得に失敗しました: 不明な支払情報形式: {type(payment)}")
            return -1
    except Exception as e:
        logger.error(f"価格の取得に失敗しました: {e}, job_id: {job.get('id', 'unknown')}")
        return -1

def price_in_range(job: Dict[str, Any], min_price: int, max_price: int) -> bool:
    """
    仕事の価格が指定範囲内かチェック
    
    Args:
        job: 仕事情報
        min_price: 最低価格
        max_price: 最高価格
        
    Returns:
        指定範囲内の場合はTrue
    """
    # フィルタリングが不要な場合
    if min_price <= 0 and max_price <= 0:
        return True
        
    price = get_job_price(job)
    
    # 価格が取得できない場合
    if price < 0:
        return False
        
    # 最低価格のみ指定されている場合
    if min_price > 0 and max_price <= 0:
        return price >= min_price
        
    # 最高価格のみ指定されている場合
    if min_price <= 0 and max_price > 0:
        return price <= max_price
        
    # 両方指定されている場合
    return min_price <= price <= max_price

def format_payment_text(job: Dict[str, Any]) -> str:
    """
    支払い情報を表示用にフォーマット
    
    Args:
        job: 仕事情報
        
    Returns:
        フォーマットされた支払い情報文字列
    """
    try:
        payment = job.get('payment', {})
        
        # payment が文字列の場合（古いデータ形式）
        if isinstance(payment, str):
            return payment
            
        # payment が辞書の場合（新しいデータ形式）
        elif isinstance(payment, dict):
            payment_type = payment.get('payment_type', '')
            
            if 'min_price' in payment and 'max_price' in payment:
                min_price = payment.get('min_price', '0')
                max_price = payment.get('max_price', '0')
                
                if min_price and max_price and min_price != max_price:
                    # 「5,000円 〜 10,000円」のような形式
                    return f"{min_price:,}円 〜 {max_price:,}円"
                elif min_price:
                    # 「5,000円」のような形式
                    return f"{min_price:,}円"
                elif max_price:
                    # 「〜 5,000円」のような形式
                    return f"〜 {max_price:,}円"
                    
            # payment_typeが指定されている場合はそれを使用
            if payment_type:
                return payment_type
                
            # それ以外の場合
            return "要相談"
        else:
            return "報酬情報なし"
            
    except Exception as e:
        logger.error(f"支払い情報の整形中にエラーが発生しました: {e}, job_id: {job.get('id', 'unknown')}")
        return "報酬情報の取得に失敗" 