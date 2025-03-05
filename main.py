#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
クラウドワークス案件モニターアプリケーション

クラウドワークスの新着案件を定期的に取得して表示し、
条件に合った案件があればメール通知を行うアプリケーションです。
"""

import os
import re
import sys
import json
import time
import queue
import logging
import threading
import webbrowser
import smtplib
import shutil
import subprocess
from queue import Queue
from typing import List, Dict, Any, Optional, Callable
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone

import flet as ft
from flet import (
    Page, Text, Column, Row, Container, TextField, ElevatedButton, 
    ProgressBar, Checkbox, ListView, Tab, Tabs, Card, MainAxisAlignment,
    ProgressRing
)

from job_scraper import CrowdworksJobScraper
from job_storage import JobStorage
# 新しく作成したモジュールをインポート
from job_utils import (
    parse_date, format_date, get_job_date_for_sorting,
    is_within_days, get_job_price, price_in_range, format_payment_text
)
from ui_components import (
    create_job_card, show_notification, update_status, create_settings_tab
)

# 日本のタイムゾーン
JST = timezone(timedelta(hours=9))

# ロガー設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('crowdworks_monitor.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class JobMonitorApp:
    """
    クラウドワークス案件モニターアプリケーションのメインクラス
    
    このクラスは、クラウドワークスの案件を監視し、フィルタリングして表示するための
    機能を提供します。定期的な更新、検索フィルタリング、通知などの機能を持ちます。
    """
    
    # 人気キーワードのリスト（実際には動的に更新される）
    POPULAR_KEYWORDS = ["Python", "データ分析", "AI", "機械学習", "Webスクレイピング"]
    
    def __init__(self, page: ft.Page):
        """
        クラウドワークス案件モニターアプリケーションの初期化
        
        Args:
            page: Fletのページオブジェクト
        """
        # 処理開始メッセージ
        logger.info("アプリケーションの初期化を開始")
        
        try:
            # ページオブジェクトの設定
            self.page = page
            
            # スクレイパーとストレージの初期化
            self.scraper = CrowdworksJobScraper()
            self.storage = JobStorage()
            
            # スレッド管理
            self.scheduler_thread = None
            self.is_running = False
            self.is_scheduler_running = False  # スケジューラー実行状態
            
            # スレッド間通信用のキュー
            self.ui_update_queue = queue.Queue()
            
            # フィルタリング設定
            self.filter_keywords = []
            self.filter_days = 7
            self.notification_enabled = True
            self.min_price = 0
            self.max_price = 0  # 0の場合は上限なし
            
            # 初期化フラグ
            self.simulation_mode_switch = None
            self.auto_fallback_switch = None
            
            # メール通知設定
            self.email_config = self._load_email_config()
            
            # UIコンポーネント
            self._init_ui_components()
            
            # アプリ初期化
            self._init_app()
            
            # UI更新タイマー設定
            self._setup_ui_update_timer()
            
            logger.info("アプリケーションの初期化が完了しました")
            
        except Exception as e:
            logger.error(f"アプリケーションの初期化中にエラーが発生しました: {e}", exc_info=True)
            raise
    
    def _load_email_config(self) -> Dict[str, Any]:
        """
        メール設定を読み込む
        
        Returns:
            メール設定の辞書
        """
        config_path = "email_config.json"
        default_config = {
            "enabled": False,
            "smtp_server": "smtp.gmail.com",
            "smtp_port": 587,
            "username": "your.email@example.com",
            "password": "",
            "recipient": "",
            "from_name": "クラウドワークス案件モニター"
        }
        
        try:
            if not os.path.exists(config_path) or os.path.getsize(config_path) == 0:
                # ファイルが存在しないか空の場合、デフォルト設定を保存して返す
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
                logger.info("デフォルトのメール設定ファイルを作成しました")
                return default_config
                
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info("メール設定を読み込みました")
                return config
        except json.JSONDecodeError:
            # JSON形式が不正な場合
            logger.error("メール設定の読み込みに失敗しました: 不正なJSON形式です")
            # バックアップを作成して新しいファイルを生成
            if os.path.exists(config_path):
                backup_path = f"{config_path}.bak"
                try:
                    shutil.copy(config_path, backup_path)
                    logger.info(f"不正なメール設定ファイルを{backup_path}にバックアップしました")
                except Exception as e:
                    logger.error(f"バックアップの作成に失敗しました: {e}")
            # デフォルト設定を保存
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            logger.info("デフォルトのメール設定ファイルを作成しました")
            return default_config
        except Exception as e:
            logger.error(f"メール設定の読み込みに失敗しました: {e}")
            return default_config
    
    def _load_email_settings(self) -> Dict[str, Any]:
        """
        メール設定を読み込む
        
        Returns:
            メール設定の辞書
        """
        config_path = "email_config.json"
        default_config = {
            "enabled": False,
            "gmail_address": "",
            "gmail_app_password": "",
            "recipient": "",
            "simulation_mode": True,  # デフォルトでシミュレーションモード有効
            "auto_fallback": True,    # デフォルトで自動フォールバック有効
            "subject_template": "クラウドワークスで{count}件の新着案件があります"
        }
        
        try:
            if not os.path.exists(config_path) or os.path.getsize(config_path) == 0:
                # ファイルが存在しないか空の場合、デフォルト設定を保存して返す
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
                logger.info("デフォルトのメール設定ファイルを作成しました")
                return default_config
                
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                logger.info("メール設定を読み込みました")
                return config
        except json.JSONDecodeError:
            # JSON形式が不正な場合
            logger.error("メール設定の読み込みに失敗しました: 不正なJSON形式です")
            # バックアップを作成して新しいファイルを生成
            if os.path.exists(config_path):
                backup_path = f"{config_path}.bak"
                try:
                    shutil.copy(config_path, backup_path)
                    logger.info(f"不正なメール設定ファイルを{backup_path}にバックアップしました")
                except Exception as e:
                    logger.error(f"バックアップの作成に失敗しました: {e}")
            # デフォルト設定を保存
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            logger.info("デフォルトのメール設定ファイルを作成しました")
            return default_config
        except Exception as e:
            logger.error(f"メール設定の読み込みに失敗しました: {e}")
            return default_config
    
    def _save_email_config(self):
        """メール設定を保存する"""
        try:
            with open("email_config.json", "w", encoding="utf-8") as f:
                json.dump(self.email_config, f, indent=2, ensure_ascii=False)
            logger.info("メール設定を保存しました")
        except Exception as e:
            logger.error(f"メール設定の保存に失敗しました: {e}")
    
    def _init_ui_components(self):
        """UIコンポーネントの初期化"""
        logging.info("UIコンポーネントを初期化中...")
        self.is_search_cancelled = False
        
        # メール設定の読み込み
        try:
            self.email_config = self._load_email_settings()
            logging.info(f"メール設定を読み込みました: {self.email_config}")
        except Exception as e:
            logging.error(f"メール設定の読み込みに失敗しました: {e}")
            self.email_config = {"enabled": False}
        
        # ステータステキスト
        self.status_text = ft.Text("準備完了", color=ft.colors.GREEN)
        
        # 案件リスト表示用コンテナ
        self.job_list = ft.ListView(
            expand=True,
            spacing=10,
            padding=20,
            auto_scroll=False,  # 自動スクロールを無効化
            on_scroll=self._handle_list_scroll,  # スクロールイベントハンドラを追加
        )
        
        # 検索フィールド
        self.search_field = ft.TextField(
            label="検索キーワード（カンマ区切り）",
            hint_text="例: Python, データ分析",
            expand=True
        )
        
        # 人気キーワードチップ
        self.keyword_chips = ft.Row(
            controls=[
                ft.ElevatedButton(
                    text=keyword,
                    on_click=lambda e, kw=keyword: self._add_keyword_chip(kw),
                    style=ft.ButtonStyle(
                        shape=ft.RoundedRectangleBorder(radius=20),
                        padding=5,
                        color=ft.colors.WHITE,
                        bgcolor=[
                            ft.colors.BLUE_400,
                            ft.colors.INDIGO_400,
                            ft.colors.PURPLE_400,
                            ft.colors.DEEP_PURPLE_400,
                            ft.colors.TEAL_400,
                        ][i % 5],  # 5種類の色をローテーション
                        elevation=2,
                    ),
                    height=35
                ) for i, keyword in enumerate(self.POPULAR_KEYWORDS[:5])  # 最初の5つだけ表示
            ],
            wrap=True,
            spacing=8,
        )
        
        # 検索ボタン
        self.search_button = ft.ElevatedButton(
            "検索",
            icon=ft.icons.SEARCH,
            style=ft.ButtonStyle(
                bgcolor=ft.colors.INDIGO_600,
                color=ft.colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation=3,
                animation_duration=300,
            ),
            on_click=self._handle_search_click,
        )
        
        # 検索キャンセルボタン
        self.search_cancel_button = ft.ElevatedButton(
            "検索中断",
            icon=ft.icons.CANCEL,
            style=ft.ButtonStyle(
                bgcolor=ft.colors.RED_600,
                color=ft.colors.WHITE,
                shape=ft.RoundedRectangleBorder(radius=8),
                elevation=3,
                animation_duration=300,
            ),
            on_click=self._handle_search_cancel,
            visible=False,  # 初期状態では非表示
        )
        
        # フィルター関連のフィールド
        # 料金範囲指定
        self.min_price_field = ft.TextField(
            label="最低報酬（円）",
            hint_text="例: 5000",
            width=150,
            input_filter=ft.NumbersOnlyInputFilter()
        )
        
        self.max_price_field = ft.TextField(
            label="最高報酬（円）",
            hint_text="上限なし",
            width=150,
            input_filter=ft.NumbersOnlyInputFilter()
        )
        
        # 日付ドロップダウン
        self.days_dropdown = ft.Dropdown(
            label="表示期間",
            width=150,
            options=[
                ft.dropdown.Option("1", "1日"),
                ft.dropdown.Option("3", "3日"),
                ft.dropdown.Option("7", "7日"),
                ft.dropdown.Option("14", "14日"),
                ft.dropdown.Option("30", "30日"),
                ft.dropdown.Option("0", "全期間")
            ],
            value="7"
        )
        
        # 通知スイッチ
        self.notification_switch = ft.Switch(
            label="通知",
            value=True,
            active_color=ft.colors.TEAL_400
        )
        
        # 操作ボタン
        self.refresh_button = ft.ElevatedButton(
            text="今すぐ更新",
            icon=ft.icons.REFRESH,
            on_click=self._handle_refresh_click,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                color=ft.colors.WHITE,
                bgcolor=ft.colors.DEEP_PURPLE_500,
                elevation=5,
                shadow_color=ft.colors.DEEP_PURPLE_900,
                animation_duration=300,  # アニメーション時間（ミリ秒）
            ),
        )
        
        self.start_button = ft.ElevatedButton(
            text="自動更新開始",
            icon=ft.icons.PLAY_ARROW,
            on_click=self._handle_start_click,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                color=ft.colors.WHITE,
                elevation=5,
                shadow_color=ft.colors.TEAL_900,
                animation_duration=300,  # アニメーション時間（ミリ秒）
            ),
            bgcolor=ft.colors.TEAL_600
        )
        
        self.stop_button = ft.ElevatedButton(
            text="停止",
            icon=ft.icons.STOP,
            on_click=self._handle_stop_click,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                color=ft.colors.WHITE,
                elevation=5,
                shadow_color=ft.colors.RED_900,
                animation_duration=300,  # アニメーション時間（ミリ秒）
            ),
            bgcolor=ft.colors.RED_600,
            disabled=True
        )
        
        # 操作ボタン行
        operation_buttons = ft.Row(
            controls=[
                self.refresh_button,
                self.start_button,
                self.stop_button
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10
        )
        
        # プログレスインジケータ
        self.progress_bar = ft.ProgressBar(
            width=400, 
            visible=False,
            color=ft.colors.AMBER
        )
        
        # プログレスリング（互換性のため）
        self.progress_ring = ft.ProgressRing(
            width=20, 
            height=20, 
            stroke_width=2,
            visible=False
        )
        
        # メイン列の作成
        main_column = ft.Column(
            controls=[
                # ツールバー
                ft.Row(
                    controls=[
                        self.search_field,
                        self.search_button
                    ],
                    alignment=ft.MainAxisAlignment.CENTER
                ),
                # キーワードチップ
                self.keyword_chips,
                # プログレスとステータス
                ft.Row(
                    controls=[
                        self.progress_bar,
                        self.progress_ring
                    ],
                    alignment=ft.MainAxisAlignment.CENTER
                ),
                ft.Row(
                    controls=[self.status_text],
                    alignment=ft.MainAxisAlignment.CENTER
                ),
                # フィルターカードの部分を削除
                # 操作ボタン
                operation_buttons,
                # 仕事リスト
                ft.Container(
                    content=self.job_list,
                    expand=True,
                    padding=ft.padding.only(top=10)
                )
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True
        )
        
        # メール通知設定
        self.email_enabled_switch = ft.Switch(
            label="メール通知",
            value=self.email_config.get("enabled", False),
            active_color=ft.colors.GREEN,
            on_change=self._toggle_email_settings
        )
        
        # シミュレーションモードスイッチ
        self.simulation_mode_switch = ft.Switch(
            label="シミュレーションモード",
            value=self.email_config.get("simulation_mode", True),
            active_color=ft.colors.AMBER,
            on_change=self._toggle_simulation_mode
        )
        
        # 自動フォールバックスイッチ
        self.auto_fallback_switch = ft.Switch(
            label="自動フォールバック",
            value=self.email_config.get("auto_fallback", True),
            active_color=ft.colors.BLUE,
            on_change=self._toggle_auto_fallback
        )
        
        # Gmail設定フィールド（送受信兼用）
        self.gmail_address_field = ft.TextField(
            label="Gmailアドレス（送受信兼用）",
            value=self.email_config.get("gmail_address", ""),
            width=300,
            disabled=not self.email_config.get("enabled", False),
            helper_text="新着案件の通知を送受信するGmailアドレスを入力してください"
        )
        
        self.gmail_app_password_field = ft.TextField(
            label="Gmailアプリパスワード",
            value=self.email_config.get("gmail_app_password", ""),
            width=300,
            password=True,  # パスワードを隠す
            disabled=not self.email_config.get("enabled", False),
            helper_text="通常のパスワードではなく、専用のアプリパスワードを入力（16文字）"
        )
        
        self.email_save_button = ft.ElevatedButton(
            text="保存",
            on_click=self._save_email_settings,
            disabled=not self.email_config.get("enabled", False),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                color=ft.colors.WHITE,
                bgcolor=ft.colors.BLUE_600,
                elevation=3,
                shadow_color=ft.colors.BLUE_900,
                animation_duration=300,  # アニメーション時間（ミリ秒）
            ),
        )
        
        self.email_test_button = ft.ElevatedButton(
            text="テスト送信",
            on_click=self._send_test_email,
            disabled=not self.email_config.get("enabled", False),
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=8),
                color=ft.colors.WHITE,
                bgcolor=ft.colors.AMBER_600,
                elevation=3,
                shadow_color=ft.colors.AMBER_900,
                animation_duration=300,  # アニメーション時間（ミリ秒）
            ),
        )
        
        # メール通知設定
        self.email_settings_view = ft.Container(
            visible=False,
            padding=20
        )
        
        # 操作ボタンコンテナ
        self.actions_container = ft.Container(
            content=ft.Row(
                [
                    self.search_button,
                    self.search_cancel_button,  # 検索キャンセルボタンを追加
                    self.refresh_button,
                    self.start_button,
                    self.stop_button,
                    # JSONデータ表示ボタンを追加
                    ft.ElevatedButton(
                        text="JSON形式表示",
                        icon=ft.icons.DATA_OBJECT,
                        on_click=self._show_json_button_click,
                        style=ft.ButtonStyle(
                            shape=ft.RoundedRectangleBorder(radius=8),
                            color=ft.colors.WHITE,
                            bgcolor=ft.colors.INDIGO_400,
                            elevation=3,
                            shadow_color=ft.colors.INDIGO_900,
                            animation_duration=300,
                        ),
                    ),
                ],
                spacing=10,
            ),
            margin=ft.margin.only(top=10, bottom=10),
        )
        
        # 検索進捗表示用コンポーネント
        self.progress_container = ft.Container(
            content=ft.Row(
                [
                    ft.ProgressRing(
                        width=20, 
                        height=20, 
                        stroke_width=2,
                        color=ft.colors.INDIGO_400
                    ),
                    ft.Text(
                        "検索中...", 
                        size=14, 
                        color=ft.colors.INDIGO_400,
                        weight=ft.FontWeight.W_500
                    )
                ],
                spacing=10,
                alignment=ft.MainAxisAlignment.CENTER
            ),
            visible=False,
            padding=ft.padding.symmetric(vertical=10)
        )
        
        # 操作説明
        self.operation_help = ft.Container(
            content=ft.Text(
                "【ボタン説明】「開始」: 1時間ごとの自動更新を開始 / 「停止」: 自動更新を停止 / 「今すぐ更新」: 手動で更新",
                size=12,
                italic=True,
                color=ft.colors.GREY
            ),
            margin=ft.margin.only(bottom=10)
        )
    
    def _add_keyword_chip(self, keyword: str):
        """
        検索欄にキーワードを追加
        
        Args:
            keyword: 追加するキーワード
        """
        current = self.search_field.value
        if not current:
            self.search_field.value = keyword
        else:
            keywords = [k.strip() for k in current.split(",")]
            if keyword not in keywords:
                keywords.append(keyword)
                self.search_field.value = ", ".join(keywords)
        
        self.page.update()
    
    def _init_app(self):
        """アプリケーションの初期化"""
        try:
            logging.info("アプリケーションを初期化中...")
            
            # タイトル
            title = ft.Text(
                "Crowdworks案件モニター",
                size=24,
                weight=ft.FontWeight.BOLD,
                color=ft.colors.INDIGO_700
            )
            
            # フィルターコントロール
            filter_controls = ft.Row(
                controls=[
                    ft.Column(
                        controls=[
                            ft.Row(
                                [
                                    self.search_field,
                                    self.search_button,
                                    self.search_cancel_button
                                ],
                                spacing=10
                            ),
                            self.keyword_chips
                        ],
                        spacing=10,
                        expand=True
                    ),
                    ft.Column(
                        controls=[
                            ft.Row(
                                [
                                    ft.Text("期間:", size=14),
                                    self.days_dropdown,
                                    ft.Text("価格:", size=14),
                                    self.min_price_field,
                                    ft.Text("〜", size=14),
                                    self.max_price_field,
                                ],
                                spacing=5
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.END
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            )
            
            # タブ
            tabs = ft.Tabs(
                selected_index=0,
                animation_duration=300,
                expand=True,  # タブ自体を拡張
                tabs=[
                    ft.Tab(
                        text="案件一覧",
                        content=ft.Container(
                            content=self.job_list,
                            padding=10,
                            expand=True  # このコンテナを拡張
                        )
                    ),
                    ft.Tab(
                        text="設定",
                        icon=ft.icons.SETTINGS,
                        content=self._create_settings_tab()
                    ),
                ],
            )
            
            # レイアウト構築 - 垂直方向に拡張するために高さの配分を調整
            self.page.add(
                title,
                filter_controls,
                self.actions_container,
                self.operation_help,  # 操作説明を追加
                self.progress_container,  # ローディングアニメーションを追加
                ft.Divider(),
                ft.Container(
                    content=tabs,
                    expand=True  # タブを含むコンテナを拡張
                )
            )
            
            # 初期表示
            self._display_jobs()
            
            # 操作ボタンの状態を初期化
            self._update_operation_buttons_state()
        
        except Exception as e:
            logger.error(f"アプリケーションの初期化中にエラーが発生しました: {e}")
            raise
    
    def _process_ui_updates(self):
        """UIアップデートキューを処理"""
        try:
            while not self.ui_update_queue.empty():
                update_func = self.ui_update_queue.get_nowait()
                update_func()
                self.ui_update_queue.task_done()
        except Exception as e:
            logger.error(f"UI更新処理中にエラーが発生しました: {e}")
    
    def _queue_ui_update(self, update_func: Callable):
        """UI更新をキューに追加"""
        self.ui_update_queue.put(update_func)
    
    def _setup_ui_update_timer(self):
        """UI更新タイマーの設定"""
        def update_timer_callback(e):
            self._process_ui_updates()
        
        # ページの更新間隔を設定（100ms）
        self.page.on_interval = update_timer_callback
        self.page.update_interval = 100
    
    def _toggle_email_settings(self, e):
        """
        メール設定の切り替え
        
        Args:
            e: イベントオブジェクト
        """
        self.email_enabled_switch.value = e.control.value
        self._save_email_config()
        self._update_operation_buttons_state()
    
    def _handle_refresh_click(self, e):
        """
        更新ボタンがクリックされたときの処理
        
        更新中の視覚的フィードバックを提供し、更新処理を非同期で開始します。
        またメール機能が未設定の場合は設定を促します。
        """
        # メールアドレスが設定されているか確認
        if not self._check_email_setting():
            return
            
        # メール設定が無効で、過去に促していない場合はメール設定を促す
        if not self.email_config.get("enabled", False) and not hasattr(self, "_mail_prompted"):
            self._mail_prompted = True
            
            # メール設定ダイアログを表示
            def show_mail_dialog():
                dialog = ft.AlertDialog(
                    title=ft.Text("メール通知の設定"),
                    content=ft.Text("新着案件が見つかった時にメールで通知を受け取りませんか？\nメール設定を行うと、新着案件情報を自動的にメールで受け取れます。"),
                    actions=[
                        ft.TextButton("あとで", on_click=lambda _: setattr(self.page.dialog, "open", False)),
                        ft.TextButton("設定する", on_click=self._open_email_settings)
                    ],
                    actions_alignment=ft.MainAxisAlignment.END
                )
                self.page.dialog = dialog
                self.page.dialog.open = True
                self.page.update()
            
            self._queue_ui_update(show_mail_dialog)
        
        # プログレスインジケータのみを表示して処理状態を示す
        self.progress_bar.visible = True
        self.status_text.value = "更新中..."
        self.status_text.color = ft.colors.ORANGE
        self.page.update()
        
        # 非同期で更新処理を実行
        threading.Thread(target=self._fetch_jobs).start()
    
    def _check_email_setting(self) -> bool:
        """
        メール設定が有効かどうかをチェックし、無効な場合は設定を促す
        
        Returns:
            bool: メール設定が有効な場合はTrue、そうでない場合はFalse
        """
        has_valid_email = (
            self.email_enabled_switch.value and 
            self.gmail_address_field.value and 
            '@' in self.gmail_address_field.value
        )
        
        if not has_valid_email:
            self._show_notification("メールアドレスが設定されていません。設定タブからメールアドレスを登録してください。")
            self._update_status("操作前にメールアドレスを設定してください", ft.colors.AMBER)
            self.page.tabs.selected_index = 1  # 設定タブに切り替え
            self.page.update()
            return False
            
        return True
    
    def _handle_start_click(self, e):
        """開始ボタンがクリックされたときの処理"""
        # メールアドレスが設定されているか確認
        if not self._check_email_setting():
            return
            
        # UI更新のスレッドセーフな処理
        self._start_scheduler_ui_update()
        # スケジューラの起動（バックグラウンドスレッド）
        threading.Thread(target=self._start_scheduler).start()
    
    def _handle_stop_click(self, e):
        """
        停止ボタンがクリックされたときの処理
        
        スケジューラーを停止し、ボタンの状態を更新します。
        停止処理が確実に行われるようにし、即時反応するように改善します。
        """
        # メールアドレスが設定されているか確認
        if not self._check_email_setting():
            return
            
        try:
            # スケジューラが実行中でなければ何もしない
            if not self.is_scheduler_running:
                self._show_notification("自動更新は実行されていません")
                return
                
            # 停止処理中の状態を表示
            self.stop_button.bgcolor = ft.colors.RED_100
            self.status_text.value = "スケジュール更新を停止しています..."
            self.status_text.color = ft.colors.ORANGE
            self.page.update()
            
            # スケジュールを即時クリア
            import schedule
            schedule.clear()
            
            # 停止状態を設定
            self.is_scheduler_running = False
            
            # 状態を更新
            self._update_stopped_state()
            
            # 停止通知
            self._show_notification("スケジュール更新を停止しました")
            
            logger.info("スケジュール更新を停止しました")
        except Exception as e:
            logger.error(f"スケジュール停止中にエラーが発生: {e}", exc_info=True)
            self._update_status(f"停止エラー: {str(e)}", ft.colors.RED)
    
    def _update_stopped_state(self):
        """停止状態のUI更新"""
        # ボタンの状態を更新
        self.start_button.disabled = False
        self.stop_button.disabled = True
        self.status_text.value = "停止中"
        self.status_text.color = ft.colors.RED
        self.page.update()
    
    def _start_scheduler(self):
        """
        スケジューラーを開始
        
        1時間ごとに仕事情報を取得するスケジュールを開始します。
        スケジューラーの信頼性と使いやすさを向上させています。
        """
        try:
            # 既存のスケジュールをクリア（二重登録防止）
            import schedule
            schedule.clear()
            
            # スケジュールを設定
            schedule.every(1).hour.do(self._fetch_jobs)
            
            # UIを更新する関数
            def update_started_state():
                self.is_scheduler_running = True
                
                # ボタンの状態を更新
                self.start_button.bgcolor = ft.colors.GREEN_100
                self.start_button.tooltip = "スケジュール実行中"
                self.start_button.disabled = True
                self.stop_button.disabled = False
                self.stop_button.bgcolor = None
                
                # 状態を更新
                self._update_status("スケジュール更新を開始しました (1時間ごと)", ft.colors.GREEN)
            
            # UI更新をキューに入れる
            self._queue_ui_update(update_started_state)
            
            # すぐに1回目の更新を実行し、メール送信も行う
            self._fetch_jobs(initial_run=True)
            
            # スケジューラーを実行
            while self.is_scheduler_running:
                schedule.run_pending()
                time.sleep(1)
            
            logger.info("スケジューラーが停止しました")
        
        except Exception as e:
            logger.error(f"スケジューラー開始中にエラーが発生: {e}", exc_info=True)
            
            # エラー表示する関数
            def update_error_state():
                self.is_scheduler_running = False
                self.start_button.bgcolor = None
                self.start_button.tooltip = "1時間ごとの自動更新を開始します"
                self._update_status(f"スケジュールエラー: {str(e)}", ft.colors.RED)
            
            # UIアップデートキューに追加
            self._queue_ui_update(update_error_state)
    
    def _fetch_jobs(self, initial_run=False):
        """
        ジョブ情報を取得し、表示を更新
        
        CrowdWorksから最新の仕事情報を取得し、UI表示を更新します。
        スレッドセーフな処理を行い、UIの整合性を保ちます。
        
        Args:
            initial_run: 初回実行かどうか（初回実行時はメール通知を送信）
        """
        try:
            logger.info("仕事情報の取得を開始")
            
            # 進捗状態の更新
            def update_progress(message):
                def update():
                    self.status_text.value = message
                    self.page.update()
                self._queue_ui_update(update)
            
            update_progress("CrowdWorksに接続中...")
            
            # 仕事情報の取得
            jobs = self.scraper.get_job_offers()
            logger.info(f"{len(jobs)}件の仕事情報を取得")
            
            update_progress("データを保存中...")
            
            # 新着の仕事を取得
            new_jobs = self.storage.update_jobs(jobs)
            
            # UIを更新する関数
            def update_success():
                # プログレスインジケーターを非表示に
                self.progress_bar.visible = False
                
                # 結果メッセージを表示
                if len(new_jobs) > 0:
                    self._update_status(f"{len(new_jobs)}件の新しい案件が見つかりました", ft.colors.GREEN)
                    
                    # 新着ジョブがある場合はメール通知
                    if self.email_enabled_switch.value and self.gmail_address_field.value.strip() and (initial_run or len(new_jobs) > 0):
                        try:
                            filtered_jobs = self._filter_jobs(new_jobs)
                            count = len(filtered_jobs)
                            if count > 0:
                                subject = self.email_config["subject_template"].format(count=count)
                                self._send_email_notification(subject, filtered_jobs)
                        except Exception as e:
                            logger.error(f"メール通知処理でエラーが発生: {e}", exc_info=True)
                else:
                    self._update_status("新しい案件はありませんでした", ft.colors.BLUE)
                
                # 仕事情報の表示を更新
                self._display_jobs()
            
            # UIアップデートキューに追加
            self._queue_ui_update(update_success)
        
        except Exception as e:
            logger.error(f"仕事情報の取得に失敗: {e}", exc_info=True)
            
            # エラー表示する関数
            def update_error():
                self.progress_bar.visible = False
                self._update_status(f"エラー: {str(e)}", ft.colors.RED)
                
                # エラーの詳細を通知
                self._show_notification(f"仕事情報の取得に失敗しました: {str(e)}")
            
            # UIアップデートキューに追加
            self._queue_ui_update(update_error)
    
    def _update_status(self, message: str, color=ft.colors.GREEN):
        """ステータスメッセージを更新"""
        update_status(self.status_text, message, color, self.page)
    
    def _filter_jobs(self, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        仕事情報をフィルタリング
        
        Args:
            jobs: フィルタリング対象の仕事情報リスト
            
        Returns:
            フィルタリング後の仕事情報リスト
        """
        if not jobs:
            logger.info("フィルタリング対象の仕事がありません")
            return []
        
        logger.info(f"フィルタリング開始: {len(jobs)}件の仕事, 条件: 日数={self.filter_days}, キーワード={self.filter_keywords}")
        filtered_jobs = jobs
            
        # 日付でフィルタリング
        if self.filter_days > 0:
            date_filtered = []
            for job in filtered_jobs:
                if is_within_days(job, self.filter_days):
                    date_filtered.append(job)
            filtered_jobs = date_filtered
            logger.info(f"日付フィルタリング後: {len(filtered_jobs)}件")
        
        # キーワードでフィルタリング
        if self.filter_keywords:
            keyword_filtered = []
            for job in filtered_jobs:
                job_title = job.get('title', '').lower()
                job_description = job.get('description', '').lower()
                
                for keyword in self.filter_keywords:
                    keyword = keyword.lower()
                    if keyword in job_title or keyword in job_description:
                        keyword_filtered.append(job)
                        break
            filtered_jobs = keyword_filtered
            logger.info(f"キーワードフィルタリング後: {len(filtered_jobs)}件")
        
        # 料金でフィルタリング
        if self.min_price > 0 or self.max_price > 0:
            price_filtered = []
            for job in filtered_jobs:
                if price_in_range(job, self.min_price, self.max_price):
                    price_filtered.append(job)
            filtered_jobs = price_filtered
            logger.info(f"料金フィルタリング後: {len(filtered_jobs)}件")
            
        return filtered_jobs
    
    def _get_job_price(self, job: Dict[str, Any]) -> int:
        """
        仕事の料金を取得
        
        Args:
            job: 仕事情報の辞書
            
        Returns:
            料金（整数）。料金が特定できない場合は-1を返す
        """
        try:
            payment_info = job.get('payment_info', '')
            
            # 文字列の場合（新しいスクレイパー形式）
            if isinstance(payment_info, str):
                # 数字だけを抽出して返す
                import re
                
                # 「〜」記号で分割して最初の数値を取得（最低額）
                parts = payment_info.split('〜')
                if len(parts) > 1:
                    # 「5,000円 〜 10,000円」 形式の場合、最初の部分から数値を抽出
                    numbers = re.findall(r'(\d[\d,]*)', parts[0])
                    if numbers:
                        # カンマを除去して整数に変換
                        return int(numbers[0].replace(',', ''))
                        
                # 「〜 5,000円」 形式の場合は2つ目の部分から数値を抽出
                if payment_info.startswith('〜'):
                    numbers = re.findall(r'(\d[\d,]*)', payment_info)
                    if numbers:
                        return int(numbers[0].replace(',', ''))
                
                # 上記でないなら単純に数値を抽出
                numbers = re.findall(r'(\d[\d,]*)', payment_info)
                if numbers:
                    # 最初の数値を返す（カンマを除去）
                    return int(numbers[0].replace(',', ''))
                    
                # 数値がない場合
                logger.warning(f"料金情報から数値を抽出できませんでした: {payment_info}")
                return -1
            
            # 辞書形式の場合（古い形式）
            if isinstance(payment_info, dict):
                payment_type = payment_info.get('type', '')
                
                if payment_type == 'fixed_price':
                    return payment_info.get('price', 0)
                elif payment_type == 'hourly':
                    # 時給の場合は最低額を返す
                    return payment_info.get('min_price', 0)
                elif payment_type == 'writing_payment':
                    # 執筆単価の場合、単価を返す
                    return payment_info.get('price', 0)
                    
                logger.warning(f"未知の料金タイプ: {payment_type}")
                return -1
            
            # payment_infoが不正な形式の場合
            logger.warning(f"支払い情報の形式が不正: {type(payment_info)}, job_id: {job.get('id', 'unknown')}")
            return -1
            
        except Exception as e:
            logger.error(f"料金の取得に失敗しました: {e}, job_id: {job.get('id', 'unknown')}")
            logger.error(f"支払い情報の整形中にエラーが発生しました: {e}, job_id: {job.get('id', 'unknown')}")
            return "報酬情報の取得に失敗"
    
    def _format_date(self, date_str: str) -> str:
        """日付文字列を整形"""
        if not date_str:
            return "なし"
        
        dt = self._parse_date(date_str)
        if dt:
            return dt.strftime('%Y/%m/%d %H:%M')
        else:
            return "日付不明"
    
    def _show_notification(self, message: str, color=None):
        """通知を表示"""
        show_notification(self.page, message, color)
    
    def _validate_email_config(self) -> bool:
        """
        メール設定のバリデーション
        
        入力されたメール設定が有効かどうかを検証します。
        
        Returns:
            bool: 設定が有効な場合はTrue、そうでない場合はFalse
        """
        try:
            # Gmail設定のチェック
            gmail_address = self.gmail_address_field.value.strip()
            if not gmail_address or '@' not in gmail_address:
                self._show_notification("Gmailアドレスを入力してください")
                return False
                
            # アプリパスワードのチェック
            app_password = self.gmail_app_password_field.value.strip()
            if not app_password:
                self._show_notification("Gmailアプリパスワードを入力してください")
                return False
                
            # アプリパスワードの長さチェック（通常16文字）
            if len(app_password) != 16:
                self._show_notification("Gmailアプリパスワードは通常16文字です。確認してください。", ft.colors.AMBER)
                # 警告だけで続行可能
                
            return True
            
        except Exception as e:
            logger.error(f"メール設定のバリデーションに失敗しました: {e}")
            self._show_notification(f"設定の検証に失敗しました: {str(e)}")
            return False
    
    def _send_email_notification(self, subject: str, jobs: List[Dict[str, Any]], is_test: bool = False):
        """
        メール通知を送信
        
        Args:
            subject: メールの件名
            jobs: 通知する仕事情報のリスト
            is_test: テストメールかどうか
        """
        try:
            # メール設定が有効でない場合は送信しない
            if not self.email_config.get("enabled", False):
                logger.info("メール通知が無効なため、送信をスキップします")
                return
                
            # シミュレーションモードの場合
            if self.email_config.get("simulation_mode", True) and not is_test:
                logger.info("シミュレーションモードのため、実際のメール送信をスキップします")
                self._show_notification("シミュレーションモード: メール送信をシミュレートしました", ft.colors.BLUE)
                return
                
            # 送信先アドレスの取得
            recipient = self.email_config.get("recipient", "")
            if not recipient or '@' not in recipient:
                logger.error("送信先メールアドレスが設定されていません")
                self._show_notification("送信先メールアドレスが設定されていません", ft.colors.RED)
                return
                
            # 送信元情報の取得
            gmail_address = self.email_config.get("gmail_address", "")
            gmail_app_password = self.email_config.get("gmail_app_password", "")
            
            if not gmail_address or not gmail_app_password:
                logger.error("Gmailアドレスまたはアプリパスワードが設定されていません")
                self._show_notification("Gmailアドレスまたはアプリパスワードが設定されていません", ft.colors.RED)
                return
                
            # メール本文の作成
            if is_test:
                body = "これはクラウドワークス案件モニターからのテストメールです。\n\nメール通知設定が正常に機能しています。"
            else:
                # 仕事情報からメール本文を作成
                body = f"クラウドワークスで{len(jobs)}件の新着案件が見つかりました。\n\n"
                
                for i, job in enumerate(jobs, 1):
                    title = job.get('title', '不明')
                    url = job.get('url', '#')
                    payment = job.get('payment_info', '不明')
                    
                    body += f"{i}. {title}\n"
                    body += f"   報酬: {payment}\n"
                    body += f"   URL: {url}\n\n"
                    
                body += "\n\n--\nこのメールはクラウドワークス案件モニターによって自動送信されました。"
                
            # MIMEメッセージの作成
            msg = MIMEMultipart()
            msg['From'] = f"クラウドワークス案件モニター <{gmail_address}>"
            msg['To'] = recipient
            msg['Subject'] = subject
            
            msg.attach(MIMEText(body, 'plain', 'utf-8'))
            
            # SMTPサーバーに接続してメール送信
            try:
                server = smtplib.SMTP('smtp.gmail.com', 587)
                server.starttls()
                server.login(gmail_address, gmail_app_password)
                server.send_message(msg)
                server.quit()
                
                logger.info(f"メール通知を送信しました: {subject}")
                if is_test:
                    self._show_notification("テストメールを送信しました", ft.colors.GREEN)
                
            except Exception as smtp_error:
                logger.error(f"SMTP接続エラー: {smtp_error}")
                
                # 自動フォールバックが有効な場合、別の方法を試みる
                if self.email_config.get("auto_fallback", True):
                    logger.info("フォールバック: 別の方法でメール送信を試みます")
                    try:
                        # 別のポートを試す
                        server = smtplib.SMTP('smtp.gmail.com', 465)
                        server.starttls()
                        server.login(gmail_address, gmail_app_password)
                        server.send_message(msg)
                        server.quit()
                        
                        logger.info(f"フォールバック成功: メール通知を送信しました: {subject}")
                        if is_test:
                            self._show_notification("テストメールを送信しました (フォールバック成功)", ft.colors.GREEN)
                            
                    except Exception as fallback_error:
                        logger.error(f"フォールバック失敗: {fallback_error}")
                        if is_test:
                            self._show_notification(f"テストメール送信に失敗しました: {str(fallback_error)}", ft.colors.RED)
                        raise
                else:
                    if is_test:
                        self._show_notification(f"テストメール送信に失敗しました: {str(smtp_error)}", ft.colors.RED)
                    raise
                    
        except Exception as e:
            logger.error(f"メール通知の送信に失敗しました: {e}")
            if is_test:
                self._show_notification(f"テストメール送信に失敗しました: {str(e)}", ft.colors.RED)
            raise
    
    def _format_payment_text(self, job: Dict[str, Any]) -> str:
        """
        支払い情報を整形
        
        Args:
            job: 仕事情報の辞書
            
        Returns:
            整形された支払い情報の文字列
        """
        try:
            payment_info = job.get('payment_info', {})
            
            # payment_infoが文字列の場合は、そのまま返す
            if isinstance(payment_info, str):
                return payment_info if payment_info else "報酬情報なし"
                
            # payment_infoが辞書でない場合
            if not isinstance(payment_info, dict):
                logger.warning(f"支払い情報の形式が不正: {type(payment_info)}, job_id: {job.get('id', 'unknown')}")
                return "報酬情報なし"
            
            payment_type = payment_info.get('type', '')
            
            if payment_type == 'fixed_price':
                price = payment_info.get('price', 0)
                return f"{price:,}円"
                
            elif payment_type == 'hourly':
                min_price = payment_info.get('min_price', 0)
                max_price = payment_info.get('max_price', 0)
                
                if min_price and max_price:
                    return f"時給 {min_price:,}円 〜 {max_price:,}円"
                elif min_price:
                    return f"時給 {min_price:,}円〜"
                elif max_price:
                    return f"時給 〜{max_price:,}円"
                return "時給"
                
            elif payment_type == 'writing_payment':
                price = payment_info.get('price', 0)
                min_length = payment_info.get('min_length', 0)
                max_length = payment_info.get('max_length', 0)
                
                if price:
                    base = f"記事単価 {price:,}円"
                    if min_length and max_length:
                        return f"{base} ({min_length:,}〜{max_length:,}文字)"
                    return base
                return "記事単価"
                
            # 未知の支払い形式
            return "報酬情報あり"
        
        except Exception as e:
            logger.error(f"支払い情報の整形中にエラーが発生しました: {e}, job_id: {job.get('id', 'unknown')}")
            return "報酬情報なし"
    
    def _open_email_settings(self, e=None):
        """
        メール設定画面を開く
        """
        self.email_settings_view.visible = True
        if hasattr(self, "page") and self.page:
            if hasattr(self, "page") and hasattr(self.page, "dialog") and self.page.dialog:
                self.page.dialog.open = False
            self.page.update()
            
    def _open_url(self, url: str):
        """
        URLをブラウザで開く
        
        URLがあれば確実にブラウザで開きます。
        デバッグ情報も出力して追跡しやすくします。
        """
        try:
            if url and url != '#':
                logger.info(f"ブラウザでURLを開きます: {url}")
                # URLをエンコードして安全にする
                import urllib.parse
                if not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                
                # ブラウザで開く
                webbrowser.open(url)
                
                # 開いたことを通知
                self._show_notification(f"ブラウザで案件ページを開きました")
            else:
                logger.warning(f"無効なURLのため開けません: {url}")
                self._show_notification("有効なURLがないため開けません")
        except Exception as e:
            logger.error(f"URLを開く際にエラーが発生: {e}", exc_info=True)
            self._show_notification(f"URLを開けませんでした: {str(e)}")
    
    def _create_settings_tab(self):
        """設定タブを作成"""
        return create_settings_tab(
            self.email_enabled_switch,
            self.simulation_mode_switch,
            self.auto_fallback_switch,
            self.gmail_address_field,
            self.gmail_app_password_field,
            self.email_save_button,
            self.email_test_button,
            self._copy_instruction_text
        )
    
    def _toggle_simulation_mode(self, e):
        """シミュレーションモードの切り替え"""
        self.email_config["simulation_mode"] = self.simulation_mode_switch.value
        self._save_email_config()
    
    def _toggle_auto_fallback(self, e):
        """自動フォールバックの切り替え"""
        self.email_config["auto_fallback"] = self.auto_fallback_switch.value
        self._save_email_config()
    
    def _save_email_settings(self, e):
        """
        メール設定の保存
        """
        if self._validate_email_config():
            self.email_config["enabled"] = True
            self.email_config["gmail_address"] = self.gmail_address_field.value
            self.email_config["recipient"] = self.gmail_address_field.value  # 送受信に同じアドレスを使用
            self.email_config["gmail_app_password"] = self.gmail_app_password_field.value
            
            # シミュレーションモードを有効に
            if "simulation_mode" not in self.email_config:
                self.email_config["simulation_mode"] = True
                self.simulation_mode_switch.value = True
            
            # 自動フォールバックを有効に
            if "auto_fallback" not in self.email_config:
                self.email_config["auto_fallback"] = True
                self.auto_fallback_switch.value = True
            
            # 共通設定
            self.email_config["subject_template"] = "クラウドワークスで{count}件の新着案件があります"
            
            self._save_email_config()
            
            if hasattr(self, "email_settings_view"):
                self.email_settings_view.visible = False
            
            # シミュレーションモードが有効な場合はその旨を通知
            if self.email_config.get("simulation_mode", True):
                self._update_status(f"メール設定を保存しました（シミュレーションモード有効）", ft.colors.GREEN)
                self._show_notification("シミュレーションモードが有効です。実際にメールは送信されません。", ft.colors.BLUE)
            else:
                self._update_status(f"メール設定を保存しました", ft.colors.GREEN)
            
            # ボタンの状態を更新
            self._update_operation_buttons_state()
            
            # メール設定後に自動更新を開始するフラグがある場合
            if hasattr(self, "_start_after_mail_setting") and self._start_after_mail_setting:
                self._start_after_mail_setting = False
                # 自動更新を開始
                self._start_scheduler_ui_update()
                threading.Thread(target=self._start_scheduler).start()
            
            self.page.update()
        else:
            self._update_status("メール設定を入力してください", ft.colors.RED)
    
    def _send_test_email(self, e):
        """テストメールを送信"""
        try:
            if not self._validate_email_config():
                return
            
            self._send_email_notification(
                subject="クラウドワークス新着案件モニター - テストメール",
                jobs=[],
                is_test=True
            )
            
            self._show_notification("テストメールを送信しました")
        except Exception as e:
            logger.error(f"テストメール送信に失敗しました: {e}")
            self._show_notification(f"テストメール送信に失敗しました: {str(e)}")
    
    def _copy_instruction_text(self, e):
        """説明テキストをクリップボードにコピー"""
        instruction_text = ("※メール送信には「Gmailアプリパスワード」が必要です\n"
            "【アプリパスワードの取得方法（重要）】\n"
            "1. Googleアカウントで2段階認証を有効にする\n"
            "   https://myaccount.google.com/security にアクセス\n"
            "   「2段階認証プロセス」を選択して有効化\n"
            "2. 同じセキュリティページで「アプリパスワード」を選択\n"
            "   (「アプリパスワード」が表示されない場合は、まず2段階認証を有効にしてください)\n"
            "3. 「アプリを選択」で「その他」を選び、「CrowdWorks Monitor」と入力\n"
            "4. 「生成」ボタンをクリックし、表示された16文字のパスワードをコピー\n"
            "5. このアプリの「Gmailアプリパスワード」欄に、スペースなしで貼り付ける\n\n"
            "※最初は「シミュレーションモード」で動作確認することをお勧めします\n"
            "※通常のGmailパスワードではなく、専用の「アプリパスワード」が必要です\n"
            "※エラーが続く場合は、新しいアプリパスワードを再生成してみてください")
        self.page.set_clipboard(instruction_text)
        self._show_notification("説明テキストをクリップボードにコピーしました", ft.colors.GREEN)
    
    def _update_operation_buttons_state(self):
        """操作ボタンの状態を更新"""
        # メール設定が有効で、アドレスが設定されているかチェック
        has_valid_email = (
            self.email_enabled_switch.value and 
            self.gmail_address_field.value and 
            '@' in self.gmail_address_field.value
        )
        
        # 操作ボタンの状態を更新
        self.refresh_button.disabled = not has_valid_email
        self.start_button.disabled = not has_valid_email or self.is_scheduler_running
        self.stop_button.disabled = not has_valid_email or not self.is_scheduler_running
        
        # メールアドレスが設定されていない場合は通知
        if not has_valid_email:
            self._update_status("メールアドレスを設定してから操作を行ってください", ft.colors.AMBER)
    
    def _handle_search_cancel(self, e):
        """検索中断ボタンがクリックされたときの処理"""
        logger.info("検索処理が中断されました")
        self.is_search_cancelled = True
        self._update_status("検索が中断されました", ft.colors.RED)
        
        # ボタンの状態を元に戻す
        self._reset_search_buttons()
        self.page.update()
    
    def _reset_search_buttons(self):
        """検索関連ボタンの状態をリセット"""
        self.search_button.visible = True
        self.search_button.disabled = False
        self.search_cancel_button.visible = False
        self.refresh_button.disabled = False
        self.start_button.disabled = False
        self.progress_container.visible = False
        self.page.update()  # 状態変更を即時反映
    
    def _handle_list_scroll(self, e):
        """リストのスクロールイベントを処理"""
        # スクロール位置が極端な場合に調整
        if hasattr(e, 'pixels') and hasattr(e.control, 'scroll_to'):
            # スクロール位置が極端な場合、安全な位置に調整
            if e.pixels > 10000:  # かなり下にスクロールした場合
                e.control.scroll_to(offset=8000)
                self.page.update()
    
    def _create_job_card(self, job: Dict[str, Any]) -> ft.Card:
        """
        仕事情報からカードを作成
        
        Args:
            job: 仕事情報
            
        Returns:
            作成されたカード
        """
        return create_job_card(
            job, 
            format_date,  # 日付フォーマット関数
            format_payment_text,  # 支払い情報フォーマット関数
            self._open_url  # URL開く関数
        )
        
    def _display_jobs(self):
        """
        仕事情報をUIに表示（初期表示用）
        
        フィルタリングされた仕事情報を取得し、UI上に表示します。
        仕事は公開日時の新しい順（降順）に並び替えられます。
        """
        try:
            # 処理開始のログ
            logger.info("案件表示処理を開始")
            
            # 全ての仕事を取得
            all_jobs = self.storage.get_all_jobs()
            logger.info(f"保存済みの仕事数: {len(all_jobs)}件")
            
            # 保存された仕事がない場合、初期表示としてクラウドワークスから取得を試みる
            if not all_jobs:
                logger.info("保存されている仕事がないため、クラウドワークスから取得を試みます")
                try:
                    # ステータス更新
                    update_status(self.status_text, "クラウドワークスから最新データを取得中...", ft.colors.ORANGE, self.page)
                    self.progress_container.visible = True
                    self.page.update()
                    
                    # クラウドワークスから仕事情報を取得
                    jobs = self.scraper.get_job_offers()
                    logger.info(f"クラウドワークスから取得した仕事数: {len(jobs)}件")
                    
                    # 取得した仕事を保存
                    self.storage.update_jobs(jobs)
                    
                    # 取得した仕事を表示する
                    self._display_search_jobs(jobs)
                    
                    # 進捗表示を非表示に
                    self.progress_container.visible = False
                    self.page.update()
                    
                    # 処理完了
                    logger.info("案件表示処理が完了しました")
                    return
                except Exception as e:
                    logger.error(f"クラウドワークスからの仕事取得に失敗しました: {e}")
                    # 進捗表示を非表示に
                    self.progress_container.visible = False
                    self.page.update()
            
            # フィルタリング
            if not all_jobs:
                logger.info("フィルタリング対象の仕事がありません")
                # 案件がない場合のメッセージ
                self.job_list.controls = []
                self.job_list.controls.append(
                    ft.Container(
                        content=ft.Text("保存されている案件がありません。\n検索または更新ボタンをクリックして案件を取得してください。", 
                            color=ft.colors.GREY, size=16, text_align=ft.TextAlign.CENTER),
                        alignment=ft.alignment.center,
                        padding=40,
                        margin=ft.margin.only(top=50)
                    )
                )
                update_status(self.status_text, "案件がありません。検索または更新してください", ft.colors.BLUE, self.page)
                self.page.update()
                logger.info("案件表示処理が完了しました")
                return
                
            filtered_jobs = self._filter_jobs(all_jobs)
            logger.info(f"フィルタリング後の仕事数: {len(filtered_jobs)}件")
            
            # 例外処理を追加して、日付のパースエラーでも処理が止まらないようにする
            try:
                # 日付の新しい順に並べ替え
                filtered_jobs.sort(
                    key=get_job_date_for_sorting,
                    reverse=True  # 降順（新しい順）
                )
                logger.info("仕事の並べ替えが完了しました")
            except Exception as e:
                logger.error(f"仕事の並べ替え中にエラーが発生: {e}")
                # 並べ替えに失敗してもプロセスを続行
            
            # UIの更新を開始
            logger.info("UI更新処理を開始")
            
            # リストをクリア
            self.job_list.controls.clear()
            
            # 進捗表示
            job_count = len(filtered_jobs)
            update_status(self.status_text, f"{job_count}件の案件を表示中...", ft.colors.BLUE, self.page)
            
            # 仕事カードを追加
            for i, job in enumerate(filtered_jobs):
                try:
                    self.job_list.controls.append(self._create_job_card(job))
                    # 10件ごとに進捗更新
                    if (i + 1) % 10 == 0:
                        update_status(self.status_text, f"{i + 1}/{job_count}件の案件を表示中...", ft.colors.BLUE, self.page)
                        self.page.update()
                except Exception as e:
                    logger.error(f"カード作成中にエラーが発生しました: {e}, job_id: {job.get('id', 'unknown')}")
                    # 1つのカードの作成に失敗しても、他のカードの処理を続行
            
            # 案件がない場合のメッセージ
            if not filtered_jobs:
                self.job_list.controls.append(
                    ft.Container(
                        content=ft.Text("条件に一致する案件がありません", color=ft.colors.GREY, size=16),
                        alignment=ft.alignment.center,
                        padding=40
                    )
                )
            
            # 完了ステータスの更新
            update_status(self.status_text, f"{len(filtered_jobs)}件の案件を表示中", ft.colors.GREEN, self.page)
            
            # UIを更新
            self.page.update()
            logger.info("案件表示処理が完了しました")
            
        except Exception as e:
            logger.error(f"案件表示中にエラーが発生しました: {e}")
            show_notification(self.page, f"案件の表示に失敗しました: {str(e)}")
            # エラーステータスに更新
            update_status(self.status_text, f"表示エラー: {str(e)}", ft.colors.RED, self.page)
    
    def _display_search_jobs(self, jobs: List[Dict[str, Any]], show_json_data: bool = False):
        """
        検索結果の仕事情報をUIに表示
        
        Args:
            jobs: クラウドワークスから直接取得した仕事情報
            show_json_data: 取得した仕事情報をJSON形式で表示するかどうか
        """
        try:
            # 処理開始のログ
            logger.info("検索結果表示処理を開始")
            
            # show_json_dataフラグが有効の場合、jobs_data.jsonから直接データを読み込んで表示
            if show_json_data:
                logger.info("jobs_data.jsonから直接データを読み込みます")
                storage_jobs = self.storage.get_all_jobs()
                if storage_jobs:
                    logger.info(f"jobs_data.jsonから{len(storage_jobs)}件の仕事情報を読み込みました")
                    
                    # 表示の更新
                    self.job_list.controls = []
                    
                    # 各案件の情報を表示
                    for job in storage_jobs:
                        self.job_list.controls.append(self._create_json_card(job))
                    
                    # 完了ステータスの更新
                    update_status(self.status_text, f"jobs_data.jsonから{len(storage_jobs)}件の案件を表示中", ft.colors.GREEN, self.page)
                    self.page.update()
                    logger.info("jobs_data.jsonからの案件表示処理が完了しました")
                    return
                else:
                    logger.warning("jobs_data.jsonにデータがありません")
                    self._show_notification("jobs_data.jsonにデータがありません", ft.colors.AMBER)
            
            # 通常の検索処理
            logger.info(f"検索前の仕事数: {len(jobs)}件")
            
            # クラウドワークスから取得した件数を表示
            update_status(self.status_text, f"クラウドワークスから取得した仕事数: {len(jobs)}件", ft.colors.BLUE, self.page)
            
            # 何も検索条件がない場合はすべて表示
            if not self.filter_keywords and self.filter_days == 0 and self.min_price == 0 and self.max_price == 0:
                filtered_jobs = jobs
                logger.info("検索条件が指定されていないため、すべての結果を表示します")
            else:
                # フィルタリング
                logger.info(f"フィルタリング開始: {len(jobs)}件の仕事, 条件: 日数={self.filter_days}, キーワード={self.filter_keywords}")
                
                # 日付フィルタリング（取得した日から指定日数以内）
                filtered_jobs = []
                if self.filter_days > 0:  # 日数が0の場合はフィルタリングしない
                    for job in jobs:
                        if is_within_days(job, self.filter_days):
                            filtered_jobs.append(job)
                    logger.info(f"日付フィルタリング後: {len(filtered_jobs)}件")
                else:
                    filtered_jobs = jobs
                    logger.info("日付フィルタリングはスキップされました")
                
                # キーワードフィルタリング
                if self.filter_keywords:
                    # フィルタリング前の件数をログに記録
                    jobs_before_keyword = len(filtered_jobs)
                    filtered_jobs = self.scraper.search_jobs_by_keyword(filtered_jobs, self.filter_keywords)
                    logger.info(f"キーワードフィルタリング後: {len(filtered_jobs)}/{jobs_before_keyword}件")
                
                # 料金フィルタリング
                if self.min_price > 0 or self.max_price > 0:
                    jobs_before_price = len(filtered_jobs)
                    filtered_jobs = [
                        job for job in filtered_jobs 
                        if price_in_range(job, self.min_price, self.max_price)
                    ]
                    logger.info(f"料金フィルタリング後: {len(filtered_jobs)}/{jobs_before_price}件")
            
            logger.info(f"フィルタリング後の仕事数: {len(filtered_jobs)}件")
            
            # 例外処理を追加して、日付のパースエラーでも処理が止まらないようにする
            try:
                # 日付の新しい順に並べ替え
                filtered_jobs.sort(
                    key=get_job_date_for_sorting,
                    reverse=True  # 降順（新しい順）
                )
                logger.info("仕事の並べ替えが完了しました")
            except Exception as e:
                logger.error(f"仕事の並べ替え中にエラーが発生: {e}", exc_info=True)
            
            # 表示の更新
            self.job_list.controls = []
            
            if not filtered_jobs:
                # 検索結果が0件の場合のメッセージを表示
                self.job_list.controls.append(
                    ft.Container(
                        content=ft.Text(
                            "検索条件に一致する案件は見つかりませんでした。\n条件を変更して再度検索してください。",
                            size=16,
                            text_align=ft.TextAlign.CENTER,
                            color=ft.colors.GREY
                        ),
                        margin=ft.margin.only(top=50),
                        alignment=ft.alignment.center
                    )
                )
                update_status(self.status_text, "検索条件に一致する案件は見つかりませんでした", ft.colors.ORANGE, self.page)
            else:
                logger.info("UI更新処理を開始")
                for job in filtered_jobs:
                    self.job_list.controls.append(self._create_job_card(job))
                update_status(self.status_text, f"{len(filtered_jobs)}件の案件が見つかりました", ft.colors.GREEN, self.page)
            
            self.page.update()
            logger.info("案件表示処理が完了しました")
            
        except Exception as e:
            logger.error(f"案件表示処理中にエラーが発生: {e}", exc_info=True)
    
    def _create_json_card(self, job: Dict[str, Any]) -> ft.Card:
        """
        JSON形式の仕事情報からカードを作成
        
        Args:
            job: JSON形式の仕事情報
            
        Returns:
            作成されたカード
        """
        try:
            # 必要な情報を取得
            title = job.get('title', '不明')
            url = job.get('url', '#')
            description = job.get('description', '説明なし')
            category_id = job.get('category_id', '')
            expired_on = job.get('expired_on', '不明')
            last_released_at = job.get('last_released_at', '不明')
            payment_info = job.get('payment_info', '')
            client_name = job.get('client_name', '不明')
            is_employer_certification = job.get('is_employer_certification', False)
            
            # 各情報を表示するテキスト
            info_texts = [
                ft.Text(f"タイトル: {title}", size=16, weight=ft.FontWeight.BOLD, color=ft.colors.INDIGO_800),
                ft.Text(f"URL: {url}", size=14, color=ft.colors.BLUE, selectable=True),
                ft.Text(f"説明: {description[:150]}...", size=14, color=ft.colors.BLACK87),
                ft.Text(f"カテゴリID: {category_id}", size=14, color=ft.colors.GREY_700),
                ft.Text(f"掲載期限: {expired_on}", size=14, color=ft.colors.GREY_700),
                ft.Text(f"最終更新日時: {last_released_at}", size=14, color=ft.colors.GREY_700),
                ft.Text(f"報酬情報: {payment_info}", size=14, color=ft.colors.ORANGE_700),
                ft.Text(f"クライアント名: {client_name}", size=14, color=ft.colors.GREY_700),
                ft.Text(f"認証事業者: {'はい' if is_employer_certification else 'いいえ'}", size=14, color=ft.colors.GREY_700),
            ]
            
            # カード
            return ft.Card(
                content=ft.Container(
                    content=ft.Column(
                        controls=[
                            # ヘッダー
                            ft.ListTile(
                                title=ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
                                subtitle=ft.Text(f"クライアント: {client_name}", size=14),
                                trailing=ft.Row(
                                    [
                                        ft.IconButton(
                                            icon=ft.icons.OPEN_IN_NEW,
                                            tooltip="ブラウザで開く",
                                            on_click=lambda e, u=url: self._open_url(u)
                                        ),
                                        ft.IconButton(
                                            icon=ft.icons.FOLDER_OPEN,
                                            tooltip="JSONファイルを開く",
                                            on_click=lambda e: self._open_json_file(e)
                                        )
                                    ],
                                    spacing=0,
                                    width=100
                                )
                            ),
                            # 区切り線
                            ft.Divider(),
                            # JSON情報
                            ft.Container(
                                content=ft.Column(
                                    controls=info_texts,
                                    spacing=6
                                ),
                                padding=ft.padding.all(16)
                            )
                        ],
                        spacing=0
                    ),
                    padding=ft.padding.only(bottom=10)
                ),
                elevation=2,
                margin=ft.margin.only(bottom=10),
                color=ft.colors.BLUE_GREY_50
            )
        except Exception as e:
            logger.error(f"JSONカード作成中にエラーが発生しました: {e}")
            # エラー時は簡易カードを返す
            return ft.Card(
                content=ft.Container(
                    content=ft.Text(f"案件データの表示に失敗しました: {str(e)}", color=ft.colors.RED),
                    padding=10
                )
            )
    
    def _handle_search_click(self, e):
        """検索ボタンがクリックされたときの処理"""
        # 検索条件を更新
        keywords_text = self.search_field.value.strip() if self.search_field.value else ""
        self.filter_keywords = [kw.strip() for kw in keywords_text.split(",")] if keywords_text else []
        # 空の文字列を削除
        self.filter_keywords = [kw for kw in self.filter_keywords if kw]
        
        try:
            self.filter_days = int(self.days_dropdown.value) if self.days_dropdown.value else 0
        except (ValueError, TypeError):
            self.filter_days = 0
            self.days_dropdown.value = "0"
            
        self.notification_enabled = self.notification_switch.value
        
        # 料金範囲の取得
        try:
            self.min_price = int(self.min_price_field.value) if self.min_price_field.value else 0
        except ValueError:
            self.min_price = 0
            self.min_price_field.value = ""
            
        try:
            self.max_price = int(self.max_price_field.value) if self.max_price_field.value else 0
        except ValueError:
            self.max_price = 0
            self.max_price_field.value = ""
        
        logger.info(f"検索条件を更新: キーワード={self.filter_keywords}, 日数={self.filter_days}, 料金範囲={self.min_price}〜{self.max_price}")
        
        # 中断フラグをリセット
        self.is_search_cancelled = False
        
        # ボタンの表示状態を更新
        self.search_button.visible = False
        self.search_cancel_button.visible = True
        self.refresh_button.disabled = True
        self.start_button.disabled = True
        
        # 進捗表示
        self.progress_container.visible = True
        self._update_status("クラウドワークスから最新データを取得中...", ft.colors.ORANGE)
        self.page.update()
        
        # 非同期でクラウドワークスからデータを取得
        threading.Thread(target=self._fetch_search_jobs).start()
    
    def _fetch_search_jobs(self):
        """クラウドワークスから検索条件に合致する案件を取得"""
        try:
            # エラー発生時の処理を先に定義
            def update_error():
                self.progress_container.visible = False
                self._update_status("検索中にエラーが発生しました", ft.colors.RED)
                self._reset_search_buttons()
                self.page.update()
            
            def update_progress(message):
                def update():
                    if not self.is_search_cancelled:  # 中断されていない場合のみ更新
                        self.status_text.value = message
                        self.page.update()
                self._queue_ui_update(update)
            
            update_progress("検索処理を開始しています...")
            
            # 中断されていないか確認
            if self.is_search_cancelled:
                logger.info("検索処理が中断されました")
                self._reset_search_buttons()
                return
                
            update_progress("クラウドワークスに接続中...")
            
            # 中断チェックポイント
            if self.is_search_cancelled:
                logger.info("検索処理が中断されました")
                self._reset_search_buttons()
                return
                
            # キーワードフィルタリング
            keyword_str = ",".join(self.filter_keywords) if self.filter_keywords else ""
            
            update_progress(f"キーワード '{keyword_str}' で検索中...")
            
            # スクレイパーで仕事情報を取得
            jobs = self.scraper.get_job_offers()
            
            # ログに取得した仕事数を出力
            logger.info(f"クラウドワークスから取得した仕事数: {len(jobs)}件")
            
            # 取得した仕事情報をJSON形式で保存する（検索のたびに更新）
            self.storage.update_jobs(jobs)
            
            # 中断されていないか確認
            if self.is_search_cancelled:
                logger.info("検索処理が中断されました")
                self._reset_search_buttons()
                return
            
            # 取得した仕事数が50件の場合、JSON表示を有効にする
            show_json_data = len(jobs) == 50
            
            def update_search_result():
                # プログレスインジケーターを非表示に
                self.progress_container.visible = False
                
                # 結果を表示
                if not jobs:
                    self._update_status("検索条件に合致する案件は見つかりませんでした", ft.colors.ORANGE)
                else:
                    # 取得件数が50件の場合はJSON表示を有効にする
                    self._display_search_jobs(jobs, show_json_data)
                    if show_json_data:
                        self._update_status(f"jobs_data.jsonから{len(jobs)}件の案件を表示しています", ft.colors.GREEN)
                    else:
                        self._update_status(f"{len(jobs)}件の案件が見つかりました", ft.colors.GREEN)
                
                # ボタンの状態を元に戻す
                self._reset_search_buttons()
                self.page.update()
            
            # 結果更新処理をキューに追加
            self._queue_ui_update(update_search_result)
            
        except Exception as e:
            logger.error(f"検索処理中にエラーが発生しました: {e}", exc_info=True)
            # ここでupdate_errorがスコープ内にあることを確認
            try:
                self._queue_ui_update(update_error)
            except Exception as inner_e:
                logger.error(f"エラー処理中に二次的なエラーが発生しました: {inner_e}", exc_info=True)
                def emergency_reset():
                    self.progress_container.visible = False
                    self._update_status("検索中に重大なエラーが発生しました", ft.colors.RED)
                    self._reset_search_buttons()
                    self.page.update()
                self._queue_ui_update(emergency_reset)
    
    def _show_json_button_click(self, e):
        """
        JSON表示ボタンのクリックハンドラ
        
        Args:
            e: イベントオブジェクト
        """
        try:
            # JSONファイルが存在するか確認
            if not os.path.exists(self.storage.storage_file):
                self._show_notification("jobs_data.jsonファイルが見つかりません。検索を実行してデータを取得してください。", ft.colors.AMBER)
                return
                
            # ファイルから仕事情報を読み込む
            jobs = self.storage.get_all_jobs()
            
            # jobs_data.jsonの内容を表示
            if jobs:
                # ダイアログではなく、案件一覧に直接表示
                self._display_search_jobs(jobs, show_json_data=True)
                self._show_notification(f"jobs_data.jsonから{len(jobs)}件の案件を表示しました", ft.colors.GREEN)
            else:
                self._show_notification("仕事情報がありません。検索を実行してデータを取得してください。", ft.colors.AMBER)
        except Exception as e:
            logger.error(f"JSONデータ表示中にエラーが発生しました: {e}")
            self._show_notification(f"JSONデータを表示できませんでした: {str(e)}", ft.colors.RED)
    
    def _open_json_file(self, e):
        """
        jobs_data.jsonファイルをエクスプローラーで開く
        
        Args:
            e: イベントオブジェクト
        """
        try:
            file_path = os.path.abspath(self.storage.storage_file)
            
            # ファイルが存在するか確認
            if not os.path.exists(file_path):
                self._show_notification(f"ファイルが見つかりません: {file_path}", ft.colors.RED)
                return
                
            # OSに応じてファイルを開く
            if sys.platform == 'win32':
                os.startfile(file_path)
            elif sys.platform == 'darwin':  # macOS
                subprocess.call(['open', file_path])
            else:  # Linux系
                subprocess.call(['xdg-open', file_path])
                
            self._show_notification(f"jobs_data.jsonファイルを開きました", ft.colors.GREEN)
            
        except Exception as e:
            logger.error(f"ファイルを開く際にエラーが発生しました: {e}")
            self._show_notification(f"ファイルを開けませんでした: {str(e)}", ft.colors.RED)
    
    def _show_json_data(self, jobs: List[Dict[str, Any]]):
        """
        jobs_data.jsonファイルの内容を表示
        
        Args:
            jobs: クラウドワークスから直接取得した仕事情報（参照用・表示には使用しない）
        """
        try:
            # jobs_data.jsonファイルの内容を読み込む
            with open(self.storage.storage_file, "r", encoding="utf-8") as f:
                file_content = f.read()
            
            # JSON形式で表示するためのウィジェット
            json_display = ft.TextField(
                value=file_content,
                multiline=True,
                read_only=True,
                min_lines=15,
                max_lines=25,
                text_size=12,
                width=800,
                height=500,
                border=ft.InputBorder.OUTLINE,
                bgcolor=ft.colors.BLUE_GREY_50,
            )
            
            # 保存場所を表示するテキスト
            storage_path = os.path.abspath(self.storage.storage_file)
            path_text = ft.Text(f"ファイル保存場所: {storage_path}", size=14, color=ft.colors.BLUE_700)
            
            # 取得件数を表示するテキスト
            count_text = ft.Text(f"取得した仕事情報: {len(jobs)}件", size=16, weight=ft.FontWeight.BOLD)
            
            # ダイアログを作成
            dialog = ft.AlertDialog(
                title=ft.Text("jobs_data.json の内容", size=20, weight=ft.FontWeight.BOLD),
                content=ft.Column(
                    [
                        count_text,
                        path_text,
                        ft.Divider(),
                        ft.Text("JSONデータ:", size=14),
                        ft.Container(
                            content=json_display,
                            padding=10,
                        )
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    spacing=10,
                    height=600,
                ),
                actions=[
                    ft.ElevatedButton(
                        "ファイルを開く", 
                        icon=ft.icons.FOLDER_OPEN,
                        on_click=lambda e: self._open_json_file(e)
                    ),
                    ft.TextButton("閉じる", on_click=lambda e: self._close_json_dialog(e))
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            
            # ダイアログを表示
            self.page.dialog = dialog
            self.page.dialog.open = True
            self.page.update()
            
            # ログにも記録
            logger.info(f"jobs_data.jsonの内容を表示しました（{len(jobs)}件の仕事情報）")
            
        except Exception as e:
            logger.error(f"jobs_data.jsonの内容を表示する際にエラーが発生しました: {e}")
            self._show_notification(f"jobs_data.jsonの内容を表示できませんでした: {str(e)}", ft.colors.RED)
    
    def _close_json_dialog(self, e):
        """
        JSONデータを表示するダイアログを閉じる
        
        Args:
            e: イベントオブジェクト
        """
        if hasattr(self, "page") and self.page and hasattr(self.page, "dialog"):
            self.page.dialog.open = False
            self.page.update()

def main(page: ft.Page):
    """アプリケーションのエントリーポイント"""
    try:
        # アプリのタイトル設定
        page.title = "クラウドワークス新着案件モニター"
        
        # テーマ設定
        page.theme = ft.Theme(
            color_scheme_seed=ft.colors.INDIGO,
            visual_density=ft.VisualDensity.COMFORTABLE,  # ThemeVisualDensityからVisualDensityに変更
        )
        
        # ダークモード設定
        page.theme_mode = ft.ThemeMode.LIGHT
        
        # フォント設定
        page.fonts = {
            "ja": "Noto Sans JP",
            "en": "Roboto",
        }
        
        # 背景色設定
        page.bgcolor = ft.colors.INDIGO_50
        
        # 余白設定
        page.padding = 15
        
        # スクロール設定
        page.scroll = ft.ScrollMode.AUTO
        
        # アプリのインスタンス作成
        app = JobMonitorApp(page)
    except Exception as e:
        logger.error(f"アプリケーションの初期化に失敗しました: {e}")
        page.add(ft.Text(f"エラー: アプリケーションの起動に失敗しました: {str(e)}", color=ft.colors.RED))
        page.update()

if __name__ == "__main__":
    ft.app(target=main) 