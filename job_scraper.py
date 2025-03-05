#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
クラウドワークススクレイパー

このモジュールは、クラウドワークスのWebページから仕事情報を取得するための
スクレイピング機能を提供します。HTMLの解析とデータ抽出を行います。

主な機能:
- クラウドワークスのページを取得
- HTML内の仕事データをJSON形式で抽出
- 取得したデータを構造化して返却
"""

import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import re
import logging
from typing import Dict, List, Any, Optional

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CrowdworksJobScraper:
    """クラウドワークスから仕事情報を取得するクラス"""
    
    def __init__(self):
        """初期化メソッド"""
        self.base_url = "https://crowdworks.jp/public/jobs"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
    
    def _get_page_content(self, url: str) -> Optional[str]:
        """
        指定したURLのページコンテンツを取得する
        
        Args:
            url: 取得対象のURL
            
        Returns:
            ページのHTMLコンテンツ、エラー時はNone
        """
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"ページの取得に失敗しました: {e}")
            return None
    
    def _extract_job_data(self, html_content: str) -> Optional[Dict[str, Any]]:
        """
        HTML内のJobデータをJSON形式で抽出する
        
        Args:
            html_content: HTMLコンテンツ
            
        Returns:
            抽出したJob情報のDict、抽出失敗時はNone
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            vue_container = soup.find(id='vue-container')
            
            if not vue_container or not vue_container.has_attr('data'):
                logger.error("Vue containerが見つからないか、data属性がありません")
                return None
            
            # data属性からJSONデータを抽出
            data_attr = vue_container['data']
            # HTMLエンティティをデコードする
            data_attr = data_attr.replace('&quot;', '"')
            
            # JSON形式のデータを解析
            job_data = json.loads(data_attr)
            return job_data
        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            logger.error(f"Job情報の抽出に失敗しました: {e}")
            return None
    
    def get_job_offers(self) -> List[Dict[str, Any]]:
        """
        クラウドワークスから最新の仕事情報を取得する
        
        Returns:
            仕事情報のリスト
        """
        html_content = self._get_page_content(self.base_url)
        if not html_content:
            logger.error("ページの取得に失敗しました")
            return []
        
        job_data = self._extract_job_data(html_content)
        if not job_data or 'searchResult' not in job_data:
            logger.error("Job情報の抽出に失敗しました")
            return []
        
        # 取得したJob情報を整形して返す
        job_offers = []
        
        for job_offer_data in job_data['searchResult'].get('job_offers', []):
            job_offer = job_offer_data.get('job_offer', {})
            client = job_offer_data.get('client', {})
            payment_data = job_offer_data.get('payment', {})
            
            # 報酬情報の取得
            payment_info = ""
            if 'fixed_price_payment' in payment_data:
                price_data = payment_data['fixed_price_payment']
                min_budget = price_data.get('min_budget')
                max_budget = price_data.get('max_budget')
                if min_budget and max_budget:
                    payment_info = f"{min_budget}円 〜 {max_budget}円"
                elif max_budget:
                    payment_info = f"〜 {max_budget}円"
                elif min_budget:
                    payment_info = f"{min_budget}円 〜"
            elif 'hourly_payment' in payment_data:
                hourly_data = payment_data['hourly_payment']
                min_wage = hourly_data.get('min_hourly_wage')
                max_wage = hourly_data.get('max_hourly_wage')
                payment_info = f"時給 {min_wage}円 〜 {max_wage}円"
            elif 'fixed_price_writing_payment' in payment_data:
                writing_data = payment_data['fixed_price_writing_payment']
                article_price = writing_data.get('article_price')
                min_length = writing_data.get('min_articles_length')
                max_length = writing_data.get('max_articles_length')
                if article_price:
                    payment_info = f"記事単価 {article_price}円"
                    if min_length and max_length:
                        payment_info += f" ({min_length}〜{max_length}文字)"
            
            # 仕事情報の構築
            job_info = {
                'id': job_offer.get('id'),
                'title': job_offer.get('title', ''),
                'url': f"https://crowdworks.jp/public/jobs/{job_offer.get('id')}",
                'description': job_offer.get('description_digest', ''),
                'category_id': job_offer.get('category_id'),
                'expired_on': job_offer.get('expired_on', ''),
                'last_released_at': job_offer.get('last_released_at', ''),
                'payment_info': payment_info,
                'client_name': client.get('username', ''),
                'is_employer_certification': client.get('is_employer_certification', False)
            }
            
            job_offers.append(job_info)
        
        return job_offers

    def search_jobs_by_keyword(self, jobs: List[Dict[str, Any]], keywords: List[str]) -> List[Dict[str, Any]]:
        """
        キーワードに基づいて仕事を検索する
        
        Args:
            jobs: 検索対象の仕事リスト
            keywords: 検索キーワードのリスト
            
        Returns:
            検索条件に一致した仕事リスト
        """
        if not keywords:
            return jobs
        
        filtered_jobs = []
        match_count = 0
        
        # デバッグ用にキーワードを出力
        keywords_str = ', '.join(keywords)
        logger.info(f"検索キーワード: {keywords_str}")
        logger.info(f"検索対象の仕事数: {len(jobs)}件")
        
        for job in jobs:
            title = job['title']
            description = job['description']
            
            # タイトルと説明文をデバッグログに出力
            logger.debug(f"タイトル: {title}")
            logger.debug(f"説明文: {description}")
            
            for keyword in keywords:
                keyword = keyword.strip()
                if not keyword:  # 空のキーワードはスキップ
                    continue
                
                # タイトルと説明文の両方で検索（大文字小文字を区別せず）
                if (keyword in title or keyword in description):
                    filtered_jobs.append(job)
                    match_count += 1
                    logger.debug(f"一致: キーワード '{keyword}' が '{title}' に含まれています")
                    break  # 1つのキーワードが一致したら、この仕事を追加して次の仕事へ
        
        logger.info(f"キーワード検索結果: {match_count}/{len(jobs)}件が一致")
        return filtered_jobs

# 単体テスト用のコード
if __name__ == "__main__":
    scraper = CrowdworksJobScraper()
    jobs = scraper.get_job_offers()
    print(f"取得した仕事数: {len(jobs)}")
    
    if jobs:
        # 最初の5件の仕事情報を表示
        for i, job in enumerate(jobs[:5]):
            print(f"\n仕事 {i+1}:")
            print(f"タイトル: {job['title']}")
            print(f"URL: {job['url']}")
            print(f"報酬: {job['payment_info']}")
            print(f"依頼主: {job['client_name']}")
            print(f"最終更新日: {job['last_released_at']}") 