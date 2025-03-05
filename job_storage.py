#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
仕事情報ストレージモジュール

このモジュールは、クラウドワークスから取得した仕事情報を保存・管理するための
機能を提供します。新着の仕事と既存の仕事を比較・管理します。

主な機能:
- 仕事情報の保存
- 新着仕事の検出
- 条件に基づく仕事のフィルタリング
"""

import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
import logging

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class JobStorage:
    """仕事情報を保存・管理するクラス"""
    
    def __init__(self, storage_file: str = "jobs_data.json"):
        """
        初期化メソッド
        
        Args:
            storage_file: 仕事情報を保存するJSONファイルのパス
        """
        self.storage_file = storage_file
        self.jobs = {}  # id -> job_info のマッピング
        self.load_jobs()
    
    def load_jobs(self) -> None:
        """保存されている仕事情報を読み込む"""
        if os.path.exists(self.storage_file) and os.path.getsize(self.storage_file) > 0:
            try:
                with open(self.storage_file, 'r', encoding='utf-8') as f:
                    jobs_list = json.load(f)
                    # リストを辞書に変換（IDをキーにする）
                    self.jobs = {str(job['id']): job for job in jobs_list}
                logger.info(f"{len(self.jobs)}件の仕事情報を読み込みました")
            except json.JSONDecodeError as e:
                logger.error(f"仕事情報の読み込みに失敗しました: {e}")
                # 破損したファイルをバックアップ
                backup_file = f"{self.storage_file}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                try:
                    os.rename(self.storage_file, backup_file)
                    logger.info(f"破損したファイルを{backup_file}にバックアップしました")
                except Exception as rename_error:
                    logger.error(f"ファイルのバックアップに失敗しました: {rename_error}")
                
                # 新しい空のファイルを作成
                self.jobs = {}
                self.save_jobs()
            except KeyError as e:
                logger.error(f"仕事情報の形式が不正です: {e}")
                self.jobs = {}
            except Exception as e:
                logger.error(f"仕事情報の読み込み中に予期しないエラーが発生しました: {e}")
                self.jobs = {}
        else:
            logger.info("仕事情報のファイルが見つかりません。新規作成します。")
            self.jobs = {}
            # 空のファイルを作成
            self.save_jobs()
    
    def save_jobs(self) -> None:
        """仕事情報をファイルに保存する"""
        try:
            # 辞書の値（仕事情報）のリストに変換
            jobs_list = list(self.jobs.values())
            with open(self.storage_file, 'w', encoding='utf-8') as f:
                json.dump(jobs_list, f, ensure_ascii=False, indent=2)
            logger.info(f"{len(jobs_list)}件の仕事情報を保存しました")
        except Exception as e:
            logger.error(f"仕事情報の保存に失敗しました: {e}")
    
    def update_jobs(self, new_jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        新しい仕事情報でストレージを更新し、新着の仕事を返す
        
        Args:
            new_jobs: 新しい仕事情報のリスト
            
        Returns:
            新着の仕事情報のリスト
        """
        existing_ids = set(self.jobs.keys())
        new_jobs_dict = {}
        newly_added_jobs = []
        
        for job in new_jobs:
            job_id = str(job['id'])
            new_jobs_dict[job_id] = job
            
            # 新着の仕事を検出
            if job_id not in existing_ids:
                newly_added_jobs.append(job)
                logger.info(f"新着の仕事を検出: {job['title']}")
        
        # 新しい仕事情報で更新する
        self.jobs.update(new_jobs_dict)
        self.save_jobs()
        
        return newly_added_jobs
    
    def get_all_jobs(self) -> List[Dict[str, Any]]:
        """
        保存されている全ての仕事情報を取得する
        
        Returns:
            全ての仕事情報のリスト
        """
        return list(self.jobs.values())
    
    def clear_jobs(self) -> None:
        """
        保存されている全ての仕事情報を削除し、空の状態に初期化する
        """
        self.jobs = {}
        self.save_jobs()
        logger.info("仕事情報を初期化しました")
    
    def get_jobs_by_ids(self, job_ids: List[str]) -> List[Dict[str, Any]]:
        """
        指定したIDの仕事情報を取得する
        
        Args:
            job_ids: 取得したい仕事のIDリスト
            
        Returns:
            指定したIDの仕事情報のリスト
        """
        return [self.jobs[job_id] for job_id in job_ids if job_id in self.jobs]
    
    def filter_jobs_by_keywords(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        キーワードに基づいて仕事をフィルタリングする
        
        Args:
            keywords: 検索キーワードのリスト
            
        Returns:
            キーワードに一致する仕事情報のリスト
        """
        if not keywords:
            return self.get_all_jobs()
        
        filtered_jobs = []
        for job in self.jobs.values():
            for keyword in keywords:
                if (keyword.lower() in job['title'].lower() or 
                    keyword.lower() in job['description'].lower()):
                    filtered_jobs.append(job)
                    break
        
        return filtered_jobs
    
    def filter_jobs_by_date(self, days: int) -> List[Dict[str, Any]]:
        """
        最新の日付に基づいて仕事をフィルタリングする
        
        Args:
            days: 過去何日分の仕事を取得するか
            
        Returns:
            指定した日数内の仕事情報のリスト
        """
        if days <= 0:
            return self.get_all_jobs()
        
        now = datetime.now()
        filtered_jobs = []
        
        for job in self.jobs.values():
            try:
                # ISO形式の日付文字列をパース（例: 2025-03-04T04:40:33+09:00）
                last_released_str = job.get('last_released_at', '')
                if not last_released_str:
                    continue
                
                last_released = datetime.fromisoformat(last_released_str.replace('Z', '+00:00'))
                delta = now - last_released
                
                if delta.days < days:
                    filtered_jobs.append(job)
            except (ValueError, TypeError) as e:
                logger.error(f"日付の解析に失敗しました: {e}, job: {job['id']}")
        
        return filtered_jobs

# 単体テスト用のコード
if __name__ == "__main__":
    storage = JobStorage("test_jobs.json")
    
    # テスト用の仕事情報
    test_jobs = [
        {
            'id': 1,
            'title': 'テスト仕事1',
            'description': 'これはテスト用の仕事です',
            'last_released_at': datetime.now().isoformat()
        },
        {
            'id': 2,
            'title': 'Python開発者募集',
            'description': 'Pythonプログラマーを募集しています',
            'last_released_at': datetime.now().isoformat()
        }
    ]
    
    # 仕事情報の更新
    new_jobs = storage.update_jobs(test_jobs)
    print(f"新着の仕事数: {len(new_jobs)}")
    
    # キーワード検索
    python_jobs = storage.filter_jobs_by_keywords(['Python'])
    print(f"Pythonに関連する仕事数: {len(python_jobs)}")
    
    # 日付フィルタリング
    recent_jobs = storage.filter_jobs_by_date(1)
    print(f"最近1日以内の仕事数: {len(recent_jobs)}") 