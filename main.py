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
        # 検索中断フラグの初期化
        self.is_search_cancelled = False
        
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
    
    def _toggle_email_settings(self, e):
        """メール設定の有効/無効を切り替え"""
        enabled = self.email_enabled_switch.value
        self.gmail_address_field.disabled = not enabled
        self.gmail_app_password_field.disabled = not enabled
        self.email_save_button.disabled = not enabled
        self.email_test_button.disabled = not enabled
        
        # 設定変更時にボタンの状態も更新
        self._update_operation_buttons_state()
        
        self.page.update()
    
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
    
    def _setup_ui_update_timer(self):
        """UI更新タイマーの設定"""
        def update_timer_callback(e):
            self._process_ui_updates()
        
        # ページの更新間隔を設定（100ms）
        self.page.on_interval = update_timer_callback
        self.page.update_interval = 100
    
    def _init_app(self):
        """
        アプリケーションの初期化
        """
        logger.info("アプリケーションの初期化を開始")
        try:
            # ページの設定
            self.page.title = "クラウドワークス案件モニター"
            self.page.window.width = 1200
            self.page.window.min_width = 800
            self.page.window.height = 800
            self.page.window.min_height = 600
            self.page.scroll = ft.ScrollMode.AUTO
            self.page.theme_mode = ft.ThemeMode.SYSTEM
            self.page.theme = ft.Theme(
                color_scheme_seed=ft.colors.BLUE,
            )
            self.page.update()
            
            # ウィンドウサイズ設定
            self.page.window.width = 900
            self.page.window.height = 900
            self.page.window.min_width = 500
            self.page.window.min_height = 600
            
            # アプリタイトル
            title = ft.Text(
                "クラウドワークス新着案件モニター", 
                size=24, 
                weight=ft.FontWeight.BOLD,
                color=ft.colors.BLUE
            )
            
            # 検索・フィルタリングコントロール
            filter_controls = ft.Card(
                content=ft.Container(
                    content=ft.Column([
                        ft.Text("検索条件", weight=ft.FontWeight.BOLD),
                        self.keyword_chips,
                        ft.Row(
                            controls=[
                                self.search_field,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        ft.Row(
                            controls=[
                                self.min_price_field,
                                self.max_price_field,
                                self.days_dropdown,
                                self.notification_switch,
                                self.search_button
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        )
                    ]),
                    padding=10
                )
            )
            
            # メール通知設定
            email_settings = ft.Card(
                content=ft.Container(
                    content=ft.Column([
                        ft.Text("メール通知設定", weight=ft.FontWeight.BOLD),
                        self.email_enabled_switch,
                        self.simulation_mode_switch,
                        self.auto_fallback_switch,
                        ft.Text("Gmail設定（送受信兼用）", weight=ft.FontWeight.W_500, size=14),
                        self.gmail_address_field,
                        self.gmail_app_password_field,
                        ft.Row(
                            controls=[
                                self.email_save_button,
                                self.email_test_button
                            ]
                        ),
                        
                        # Gmail設定説明テキスト（コピー可能）
                        ft.Text("※以下の説明はコピーできます", size=12, color=ft.colors.GREEN),
                        
                        # SelectionAreaでテキストを選択可能にする
                        ft.SelectionArea(
                            content=ft.Column([
                                ft.TextField(
                                    value="※メール送信には「Gmailアプリパスワード」が必要です\n"
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
                                    "※エラーが続く場合は、新しいアプリパスワードを再生成してみてください",
                                    multiline=True,
                                    read_only=True,
                                    min_lines=10,
                                    max_lines=15,
                                    text_size=12,
                                    text_style=ft.TextStyle(color=ft.colors.BLACK),
                                    border=ft.InputBorder.OUTLINE,
                                    border_color=ft.colors.GREY_400,
                                    bgcolor=ft.colors.GREY_100,
                                    width=500,
                                    border_radius=8,
                                )
                            ])
                        ),
                        
                        # コピーボタン
                        ft.ElevatedButton(
                            "説明をクリップボードにコピー",
                            icon=ft.icons.COPY,
                            on_click=self._copy_instruction_text
                        )
                    ]),
                    padding=20
                )
            )
            
            # 操作ボタン
            operation_controls = ft.Row(
                controls=[
                    self.refresh_button,
                    self.start_button,
                    self.stop_button,
                    self.progress_bar,
                    self.status_text
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            )
            
            # 操作説明
            operation_help = ft.Container(
                content=ft.Text(
                    "【ボタン説明】「開始」: 1時間ごとの自動更新を開始 / 「停止」: 自動更新を停止 / 「今すぐ更新」: 手動で更新",
                    size=12,
                    italic=True,
                    color=ft.colors.GREY
                ),
                margin=ft.margin.only(bottom=10)
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
                operation_help,  # 操作説明を追加
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
    
    def _handle_search_click(self, e):
        """検索ボタンがクリックされたときの処理"""
        # 検索条件を更新
        keywords_text = self.search_field.value
        self.filter_keywords = [kw.strip() for kw in keywords_text.split(",")] if keywords_text else []
        self.filter_days = int(self.days_dropdown.value)
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
                        self.status_text.value = message  # progress_text → status_textに修正
                        self.page.update()
                self._queue_ui_update(update)
            
            update_progress("検索処理を開始しています...")
            
            # 中断されていないか確認
            if self.is_search_cancelled:
                logger.info("検索処理が中断されました")
                self._reset_search_buttons()
                return

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
            
            # 元のscraperを使用
            jobs = self.scraper.get_job_offers()
            
            # 中断されていないか確認
            if self.is_search_cancelled:
                logger.info("検索処理が中断されました")
                self._reset_search_buttons()
                return
            
            def update_search_result():
                # プログレスインジケーターを非表示に
                self.progress_container.visible = False
                
                # 結果を表示
                if not jobs:
                    self._update_status("検索条件に合致する案件は見つかりませんでした", ft.colors.ORANGE)
                else:
                    self._display_search_jobs(jobs)
                    self._update_status(f"{len(jobs)}件の案件が見つかりました", ft.colors.GREEN)
                
                # ボタンの状態を元に戻す
                self._reset_search_buttons()
                self.page.update()
            
            # 結果更新処理をキューに追加
            self._queue_ui_update(update_search_result)
            
        except Exception as e:
            logger.error(f"検索処理中にエラーが発生しました: {e}")
            # ここでupdate_errorがスコープ内にあることを確認
            try:
                self._queue_ui_update(update_error)
            except Exception as inner_e:
                logger.error(f"エラー処理中に二次的なエラーが発生しました: {inner_e}")
                def emergency_reset():
                    self.progress_container.visible = False
                    self._update_status("検索中に重大なエラーが発生しました", ft.colors.RED)
                    self._reset_search_buttons()
                    self.page.update()
                self._queue_ui_update(emergency_reset)
    
    def _display_search_jobs(self, jobs: List[Dict[str, Any]]):
        """
        検索結果の仕事情報をUIに表示
        
        Args:
            jobs: クラウドワークスから直接取得した仕事情報
        """
        try:
            # 処理開始のログ
            logger.info("検索結果表示処理を開始")
            
            # フィルタリング
            logger.info(f"フィルタリング開始: {len(jobs)}件の仕事, 条件: 日数={self.filter_days}, キーワード={self.filter_keywords}")
            
            # 日付フィルタリング（取得した日から指定日数以内）
            filtered_jobs = []
            for job in jobs:
                if self._is_within_days(job, self.filter_days):
                    filtered_jobs.append(job)
            
            logger.info(f"日付フィルタリング後: {len(filtered_jobs)}件")
            
            # キーワードフィルタリング
            if self.filter_keywords:
                filtered_jobs = self.scraper.search_jobs_by_keyword(filtered_jobs, self.filter_keywords)
            
            # 料金フィルタリング
            if self.min_price > 0 or self.max_price > 0:
                filtered_jobs = [
                    job for job in filtered_jobs 
                    if self._price_in_range(job, self.min_price, self.max_price)
                ]
            
            logger.info(f"フィルタリング後の仕事数: {len(filtered_jobs)}件")
            
            # 例外処理を追加して、日付のパースエラーでも処理が止まらないようにする
            try:
                # 日付の新しい順に並べ替え
                filtered_jobs.sort(
                    key=self._get_job_date_for_sorting,
                    reverse=True  # 降順（新しい順）
                )
                logger.info("仕事の並べ替えが完了しました")
            except Exception as e:
                logger.error(f"仕事の並べ替え中にエラーが発生: {e}")
            
            # 表示の更新
            self.job_list.controls = []
            logger.info("UI更新処理を開始")
            
            for job in filtered_jobs:
                self.job_list.controls.append(self._create_job_card(job))
            
            self.page.update()
            logger.info("案件表示処理が完了しました")
            
        except Exception as e:
            logger.error(f"案件表示処理中にエラーが発生: {e}", exc_info=True)
            self._update_status(f"エラー: {str(e)}", ft.colors.RED)
    
    def _price_in_range(self, job: Dict[str, Any], min_price: int, max_price: int) -> bool:
        """
        料金が指定範囲内かどうかを判定
        
        Args:
            job: 仕事情報
            min_price: 最小料金（0は制限なし）
            max_price: 最大料金（0は制限なし）
            
        Returns:
            料金が範囲内の場合はTrue
        """
        job_price = self._get_job_price(job)
        
        # 料金情報がない場合は除外
        if job_price < 0:
            return False
            
        # 最小料金チェック（min_price が 0 の場合は制限なし）
        if min_price > 0 and job_price < min_price:
            return False
            
        # 最大料金チェック（max_price が 0 の場合は制限なし）
        if max_price > 0 and job_price > max_price:
            return False
            
        return True
    
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
        
        # プログレスインジケーターのみを表示して処理状態を示す
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
        self.status_text.value = message
        self.status_text.color = color
        self.page.update()
    
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
                if self._is_within_days(job, self.filter_days):
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
                job_price = self._get_job_price(job)
                
                # 最低料金のチェック
                if self.min_price > 0 and job_price < self.min_price:
                    continue
                
                # 最高料金のチェック（0の場合は上限なし）
                if self.max_price > 0 and job_price > self.max_price:
                    continue
                
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
            料金（整数）。料金が特定できない場合は0を返す
        """
        try:
            payment_info = job.get('payment_info', {})
            
            # 文字列の場合
            if isinstance(payment_info, str):
                # 数字だけを抽出して返す
                import re
                numbers = re.findall(r'\d+', payment_info)
                if numbers:
                    return int(numbers[0])
                return 0
            
            # 辞書でない場合
            if not isinstance(payment_info, dict):
                logger.warning(f"支払い情報の形式が不正: {type(payment_info)}, job_id: {job.get('id', 'unknown')}")
                return 0
            
            payment_type = payment_info.get('type', '')
            
            if payment_type == 'fixed_price':
                return payment_info.get('price', 0)
            elif payment_type == 'hourly':
                # 時給の場合は最低額を返す
                return payment_info.get('min_price', 0)
            elif payment_type == 'writing_payment':
                # 執筆単価の場合、単価を返す
                return payment_info.get('price', 0)
            else:
                return 0
        except Exception as e:
            logger.error(f"料金の取得に失敗しました: {e}, job_id: {job.get('id', 'unknown')}")
            return 0
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """
        日付文字列をパースしてdatetimeオブジェクトを返す
        常にタイムゾーン情報を持ったdatetimeを返す
        
        Args:
            date_str: ISO形式の日付文字列
            
        Returns:
            タイムゾーン情報を持ったdatetimeオブジェクト、または None（パース失敗時）
        """
        if not date_str:
            return None
            
        try:
            # ISO形式の日付文字列をパース
            # 例: 2023-03-04T04:40:33+09:00 または 2023-03-04T04:40:33Z
            if date_str.endswith('Z'):
                # UTCのタイムゾーン情報に変換
                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                # すでにタイムゾーン情報がある
                dt = datetime.fromisoformat(date_str)
            
            return dt
        except ValueError as e:
            logger.error(f"日付文字列のパースに失敗: {e}, date_str: {date_str}")
            return None
    
    def _is_within_days(self, job: Dict[str, Any], days: int) -> bool:
        """ジョブが指定した日数以内かどうかを判定"""
        try:
            last_released_str = job.get('last_released_at', '')
            if not last_released_str:
                return False
            
            # 日付文字列をパース
            last_released = self._parse_date(last_released_str)
            if not last_released:
                return False
            
            # 現在時刻をタイムゾーン付きで取得
            now = datetime.now(timezone.utc).astimezone(JST)
            
            # タイムゾーン付きの日時同士で比較
            delta = now - last_released
            return delta.days < days
        except Exception as e:
            logger.error(f"日付処理エラー: {e}, job_id: {job.get('id', 'unknown')}")
            return False
    
    def _get_job_date_for_sorting(self, job: Dict[str, Any]) -> datetime:
        """ソート用に日付を取得（デフォルト値付き）"""
        date_str = job.get('last_released_at', '')
        dt = self._parse_date(date_str)
        if dt:
            return dt
        else:
            # パースに失敗した場合は最も古い日付を返す
            return datetime(1970, 1, 1, tzinfo=timezone.utc)
    
    def _display_jobs(self):
        """
        仕事情報をUIに表示
        
        フィルタリングされた仕事情報を取得し、UI上に表示します。
        仕事は公開日時の新しい順（降順）に並び替えられます。
        """
        try:
            # 処理開始のログ
            logger.info("案件表示処理を開始")
            
            # 全ての仕事を取得
            all_jobs = self.storage.get_all_jobs()
            logger.info(f"保存済みの仕事数: {len(all_jobs)}件")
            
            # フィルタリング
            filtered_jobs = self._filter_jobs(all_jobs)
            logger.info(f"フィルタリング後の仕事数: {len(filtered_jobs)}件")
            
            # 例外処理を追加して、日付のパースエラーでも処理が止まらないようにする
            try:
                # 日付の新しい順に並べ替え
                filtered_jobs.sort(
                    key=self._get_job_date_for_sorting,
                    reverse=True  # 降順（新しい順）
                )
                logger.info("仕事の並べ替えが完了しました")
            except Exception as e:
                logger.error(f"仕事の並べ替え中にエラーが発生しました: {e}")
                # 並べ替えに失敗してもプロセスを続行
            
            # UIの更新を開始
            logger.info("UI更新処理を開始")
            
            # リストをクリア
            self.job_list.controls.clear()
            
            # 進捗表示
            job_count = len(filtered_jobs)
            self._update_status(f"{job_count}件の案件を表示中...", ft.colors.BLUE)
            
            # 仕事カードを追加
            for i, job in enumerate(filtered_jobs):
                try:
                    self.job_list.controls.append(self._create_job_card(job))
                    # 10件ごとに進捗更新
                    if (i + 1) % 10 == 0:
                        self._update_status(f"{i + 1}/{job_count}件の案件を表示中...", ft.colors.BLUE)
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
            self._update_status(f"{len(filtered_jobs)}件の案件を表示中", ft.colors.GREEN)
            
            # UIを更新
            self.page.update()
            logger.info("案件表示処理が完了しました")
            
        except Exception as e:
            logger.error(f"案件表示中にエラーが発生しました: {e}")
            self._show_notification(f"案件の表示に失敗しました: {str(e)}")
            # エラーステータスに更新
            self._update_status(f"表示エラー: {str(e)}", ft.colors.RED)
    
    def _create_job_card(self, job: Dict[str, Any]) -> ft.Card:
        """
        求人情報からカードUIを作成
        
        Args:
            job: 求人情報の辞書
            
        Returns:
            求人情報を表示するカードUI
        """
        try:
            # デフォルト値を設定して、キーが存在しない場合にもエラーにならないようにする
            job_id = job.get('id', 'unknown')
            job_title = job.get('title', '(タイトルなし)')
            job_description = job.get('description', '(説明なし)')
            job_url = job.get('url', '#')
            is_new = job.get('is_new', False)  # 新着フラグ
            
            # URLを確実に取得
            if not job_url or job_url == '#':
                # IDがあればURLを構築
                if job_id and job_id != 'unknown':
                    job_url = f"https://crowdworks.jp/public/jobs/{job_id}"
            
            # タイトル行の作成（クリック可能にする）
            title_text = ft.Text(
                job_title,
                style=ft.TextThemeStyle.TITLE_MEDIUM,
                color=ft.colors.BLUE,
                weight=ft.FontWeight.BOLD,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
                selectable=False,  # クリックの都合上、選択不可に
            )
            
            title_row = ft.Row([
                title_text,
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            
            # タグのリスト
            tags = []
            
            # 新着の場合は新着タグを追加
            if is_new:
                tags.append(
                    ft.Container(
                        ft.Text("新着", size=12, color=ft.colors.WHITE),
                        bgcolor=ft.colors.GREEN,
                        border_radius=5,
                        padding=ft.padding.only(left=5, right=5, top=2, bottom=2),
                    )
                )
            
            # PR案件の場合はPRタグを追加
            if job.get("is_pr", False):
                tags.append(
                    ft.Container(
                        ft.Text("PR", size=12, color=ft.colors.WHITE),
                        bgcolor=ft.colors.ORANGE,
                        border_radius=5,
                        padding=ft.padding.only(left=5, right=5, top=2, bottom=2),
                    )
                )
            
            # 特急案件の場合は特急タグを追加
            if "特急" in job_title or "急募" in job_title:
                tags.append(
                    ft.Container(
                        ft.Text("急募", size=12, color=ft.colors.WHITE),
                        bgcolor=ft.colors.RED,
                        border_radius=5,
                        padding=ft.padding.only(left=5, right=5, top=2, bottom=2),
                    )
                )
            
            # タグが存在する場合はタグ行を作成
            tags_row = ft.Row(tags, spacing=5, wrap=True) if tags else None
            
            # 支払い情報を整形
            payment_text = self._format_payment_text(job)
            
            # 公開日・締切日を整形
            publish_date = self._format_date(job.get('last_released_at', ''))
            expire_date = self._format_date(job.get('expire_at', ''))
            
            # 説明の作成
            description = ft.Text(
                job_description,
                size=14,
                max_lines=3,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            
            # カード用のテキスト行を作成
            info_rows = [
                ft.Row([
                    ft.Icon(ft.icons.MONETIZATION_ON, color=ft.colors.GREEN, size=16),
                    ft.Text(payment_text, size=14),
                ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                
                ft.Row([
                    ft.Icon(ft.icons.ACCESS_TIME, color=ft.colors.BLUE, size=16),
                    ft.Text(f"公開: {publish_date} / 締切: {expire_date}", size=14),
                ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ]
            
            # クライアント情報の行を追加
            client_name = job.get("client_name", "")
            if client_name:
                client_row = ft.Row([
                    ft.Icon(ft.icons.PERSON, color=ft.colors.BLUE_GREY, size=16),
                    ft.Text(client_name, size=14),
                ], spacing=5, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                
                # クライアントが認証済みの場合は認証マークを追加
                if job.get("is_certified_client", False):
                    client_row.controls.append(
                        ft.Container(
                            ft.Icon(ft.icons.VERIFIED, color=ft.colors.BLUE, size=16),
                            tooltip="認証済みクライアント",
                            margin=ft.margin.only(left=5),
                        )
                    )
                
                info_rows.append(client_row)
            
            # 情報行をまとめる
            info_column = ft.Column(info_rows, spacing=5)
            
            # "詳細を見る"ボタンを追加
            details_button = ft.ElevatedButton(
                "詳細を見る",
                icon=ft.icons.OPEN_IN_NEW,
                on_click=lambda e, url=job_url: self._open_url(url),
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=8),
                    color=ft.colors.WHITE,
                    bgcolor=ft.colors.CYAN_700,
                    elevation=2,
                    shadow_color=ft.colors.CYAN_900,
                    animation_duration=300,  # アニメーション時間（ミリ秒）
                ),
            )
            
            # カードの内容をまとめる
            content_controls = [title_row]
            if tags_row:
                content_controls.append(tags_row)
            content_controls.extend([description, info_column, ft.Row([details_button], alignment=ft.MainAxisAlignment.END)])
            
            # カード用コンテンツを作成
            content = ft.Container(
                ft.Column(content_controls, spacing=10),
                padding=10
            )
            
            # カードの作成
            card = ft.Card(
                content=content,
                elevation=2,
                margin=ft.margin.only(bottom=10),
                data=job,  # データをカードに添付
            )
            
            # カード全体のクリックイベントも設定（冗長性のため）
            final_url = job_url  # ラムダ内でのキャプチャ用に変数を定義
            card.on_click = lambda e: self._open_url(final_url)
            
            return card
        
        except Exception as e:
            logger.error(f"カードの作成中にエラーが発生: {e}", exc_info=True)
            # エラーが発生した場合はエラーメッセージを含むシンプルなカードを返す
            return ft.Card(
                content=ft.Container(
                    ft.Column([
                        ft.Text("カードの表示エラー", style=ft.TextThemeStyle.TITLE_MEDIUM, color=ft.colors.RED),
                        ft.Text(f"エラー: {str(e)}", size=14),
                    ], spacing=10),
                    padding=10
                ),
                elevation=1,
                margin=ft.margin.only(bottom=10),
            )
    
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
                return f"固定報酬: {price:,}円"
            elif payment_type == 'hourly':
                min_price = payment_info.get('min_price', 0)
                max_price = payment_info.get('max_price', 0)
                return f"時給: {min_price:,}円〜{max_price:,}円"
            elif payment_type == 'writing_payment':
                price = payment_info.get('price', 0)
                unit = payment_info.get('unit', '文字')
                return f"執筆報酬: {price:,}円/{unit}"
            else:
                return "報酬情報なし"
        except Exception as e:
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
        try:
            # 最新のFlet推奨方法でSnackBarを表示する
            snack = ft.SnackBar(
                content=ft.Text(message, color=color),
                action="閉じる",
                open=True
            )
            # overlayに追加して表示
            if hasattr(self.page, "overlay") and self.page.overlay is not None:
                self.page.overlay.append(snack)
                self.page.update()
            else:
                logger.warning("page.overlayにアクセスできません。通知が表示されない可能性があります。")
        except Exception as e:
            logger.error(f"通知の表示に失敗しました: {e}")
    
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
                
            if not gmail_address.lower().endswith('@gmail.com'):
                self._show_notification("@gmail.comのアドレスを使用してください")
                return False
            
            # メールアドレスのバリデーション（簡易チェック）
            email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_pattern, gmail_address):
                self._show_notification("メールアドレスの形式が正しくありません")
                return False
            
            gmail_app_password = self.gmail_app_password_field.value
            if not gmail_app_password:
                self._show_notification("Gmailアプリパスワードを入力してください")
                return False
            
            # アプリパスワードは通常16文字
            if len(gmail_app_password) < 12:
                self._show_notification("Gmailアプリパスワードが短すぎます。正しいアプリパスワードを確認してください", ft.colors.AMBER)
            
            return True
        
        except Exception as e:
            logger.error(f"メール設定のバリデーション中にエラーが発生: {e}", exc_info=True)
            self._show_notification(f"メール設定の検証中にエラーが発生しました: {str(e)}")
            return False
    
    def _send_email_notification(self, subject: str, jobs: List[Dict[str, Any]], is_test: bool = False):
        """
        メール通知を送信
        
        Args:
            subject: メールの件名
            jobs: 新着案件のリスト
            is_test: テストメールかどうか
        """
        try:
            if not is_test and not self._validate_email_config():
                return
            
            # メール設定の取得
            gmail_address = self.email_config.get("gmail_address", "")
            gmail_app_password = self.email_config.get("gmail_app_password", "")
            simulation_mode = self.email_config.get("simulation_mode", False)
            
            # Gmailアドレスがない場合は中止
            if not gmail_address:
                logger.warning("Gmailアドレスが設定されていません")
                if not is_test:
                    self._show_notification("Gmailアドレスが設定されていません。設定を確認してください。")
                return
            
            # アプリパスワードがない場合は中止
            if not gmail_app_password:
                logger.warning("Gmailアプリパスワードが設定されていません")
                self._show_notification("Gmailアプリパスワードが設定されていません。設定を確認してください。")
                return
            
            # メールの内容作成
            # テストメールの場合
            if is_test:
                text_content = "これはテストメールです。クラウドワークス新着案件モニターからのメール通知が正常に機能しています。"
                html_content = """
                <html>
                <head></head>
                <body>
                    <h2>クラウドワークス新着案件モニター - テストメール</h2>
                    <p>これはテストメールです。メール通知が正常に機能しています。</p>
                </body>
                </html>
                """
            else:
                # 新着案件の一覧
                job_list_text = ""
                job_list_html = ""
                
                for i, job in enumerate(jobs[:10]):  # 最大10件まで
                    job_title = job.get("title", "タイトルなし")
                    job_url = job.get("url", "#")
                    payment_text = self._format_payment_text(job)
                    
                    job_list_text += f"{i+1}. {job_title} - {payment_text}\n"
                    job_list_html += f"""
                    <tr>
                        <td>{i+1}</td>
                        <td><a href="{job_url}">{job_title}</a></td>
                        <td>{payment_text}</td>
                    </tr>
                    """
                
                if len(jobs) > 10:
                    job_list_text += f"\n... 他 {len(jobs) - 10} 件"
                    job_list_html += f"""
                    <tr>
                        <td colspan="3">... 他 {len(jobs) - 10} 件</td>
                    </tr>
                    """
                
                text_content = f"""
                クラウドワークスに{len(jobs)}件の新着案件があります。
                
                【新着案件一覧】
                {job_list_text}
                
                詳細はアプリケーションでご確認ください。
                """
                
                html_content = f"""
                <html>
                <head></head>
                <body>
                    <h2>クラウドワークス新着案件のお知らせ</h2>
                    <p>クラウドワークスに<strong>{len(jobs)}件</strong>の新着案件があります。</p>
                    
                    <h3>新着案件一覧</h3>
                    <table border="1" cellpadding="5">
                        <tr>
                            <th>#</th>
                            <th>タイトル</th>
                            <th>報酬</th>
                        </tr>
                        {job_list_html}
                    </table>
                    
                    <p>詳細はアプリケーションでご確認ください。</p>
                </body>
                </html>
                """
            
            # メールの作成
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject or "クラウドワークス新着案件のお知らせ"
            msg["From"] = gmail_address
            msg["To"] = gmail_address
            
            # MIMEテキストの作成
            part1 = MIMEText(text_content, "plain", "utf-8")
            part2 = MIMEText(html_content, "html", "utf-8")
            
            # マルチパートメッセージにテキストを追加
            msg.attach(part1)
            msg.attach(part2)
            
            # シミュレーションモードの場合
            if simulation_mode:
                logger.info(f"【シミュレーション】メール通知をシミュレートしました: {gmail_address}")
                self._show_notification(f"【シミュレーション】メール送信をシミュレートしました: {gmail_address}")
                self._update_status("【シミュレーション】メール送信完了", ft.colors.GREEN)
                
                # 最終送信時刻を更新
                if not is_test:
                    now = datetime.now(timezone.utc).astimezone(JST)
                    self.email_config["last_sent"] = now.isoformat()
                    self._save_email_config()
                return
            
            # メール送信
            self._update_status("メール送信中...", ft.colors.ORANGE)
            
            try:
                # GmailのSMTPサーバーに接続（SSLを使用）
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                    server.set_debuglevel(1)  # デバッグレベルを設定（問題の調査に役立つ）
                    # SSLモードではstarttls()は不要
                    server.login(gmail_address, gmail_app_password)
                    server.send_message(msg)
                
                logger.info(f"メール通知を送信しました: {gmail_address}")
                self._show_notification(f"メールを{gmail_address}に送信しました")
                self._update_status("メール送信完了", ft.colors.GREEN)
            except smtplib.SMTPAuthenticationError as auth_error:
                # 認証エラーに対するより詳細な情報を提供
                error_msg = str(auth_error)
                logger.error(f"メール送信認証エラー: {auth_error}")
                
                # エラーメッセージの内容に基づいて適切なアドバイスを表示
                advice = ""
                
                # アプリパスワードが必要なエラー
                if "Application-specific password required" in error_msg:
                    advice = (
                        "Gmailの2段階認証で保護されたアカウントには「アプリパスワード」が必要です：\n\n"
                        "【アプリパスワードの取得方法】\n"
                        "1. https://myaccount.google.com/security にアクセス\n"
                        "2. 「2段階認証プロセス」を選択\n"
                        "3. 下にスクロールして「アプリパスワード」を選択\n"
                        "4. アプリ名に「CrowdWorks Monitor」と入力して作成\n"
                        "5. 生成された16文字のパスワードをコピーして入力する\n\n"
                        "※スペースなしの16文字のパスワードをそのまま入力してください\n"
                        "※Googleアカウントにログインするには: https://accounts.google.com"
                    )
                # ユーザー名とパスワードが受け付けられないエラー
                elif "Username and Password not accepted" in error_msg:
                    advice = (
                        "Gmailのユーザー名またはパスワードが正しくありません：\n\n"
                        "1. Gmailアドレスを正確に入力しているか確認してください\n"
                        "2. アプリパスワードが正しいか確認してください\n"
                        "3. パスワードを手動で再入力してみてください（コピペではなく）\n"
                        "4. Googleアカウントにログインし、セキュリティに問題がないか確認してください\n\n"
                        "※通常のパスワードではなく、「アプリパスワード」を使用してください\n"
                        "※Googleアカウントにログインするには: https://accounts.google.com"
                    )
                # その他の認証エラー
                else:
                    advice = (
                        f"Gmailの認証に失敗しました。以下を確認してください：\n\n"
                        "1. Gmailアドレスが正しく入力されているか\n"
                        "2. Googleアカウントで2段階認証が有効になっているか\n"
                        "3. アプリパスワードが正しいか（16文字、スペースなし）\n"
                        "4. パスワードを手動で再入力してみてください（コピペではなく）\n\n"
                        "【アプリパスワードの取得方法】\n"
                        "1. https://myaccount.google.com/security にアクセス\n"
                        "2. 「アプリパスワード」を選択して作成\n\n"
                        "※エラーの詳細: {error_msg}"
                    )
                
                # 詳細なエラー情報をログに記録
                logger.error(f"認証エラーの詳細情報: {error_msg}")
                
                self._show_notification(advice, ft.colors.RED)
                
                # 自動フォールバックがオンの場合
                if self.email_config.get("auto_fallback", True):
                    self.email_config["simulation_mode"] = True
                    self._save_email_config()
                    self._show_notification("認証エラーのため、自動的にシミュレーションモードに切り替えました", ft.colors.AMBER)
                    
                    # シミュレーションモードで再試行
                    self._update_status("【シミュレーション】メール送信をシミュレート中...", ft.colors.ORANGE)
                    logger.info(f"【シミュレーション】メール通知をシミュレートしました: {gmail_address}")
                    self._show_notification(f"【シミュレーション】メール送信をシミュレートしました: {gmail_address}")
                    self._update_status("【シミュレーション】メール送信完了", ft.colors.GREEN)
                    
                    # 最終送信時刻を更新
                    if not is_test:
                        now = datetime.now(timezone.utc).astimezone(JST)
                        self.email_config["last_sent"] = now.isoformat()
                        self._save_email_config()
                else:
                    self._update_status("メール送信に失敗しました", ft.colors.RED)
            except Exception as mail_error:
                # その他のエラー
                logger.error(f"メール送信エラー: {mail_error}")
                self._show_notification(f"メール送信に失敗しました: {str(mail_error)}")
                self._update_status("メール送信に失敗しました", ft.colors.RED)
                
                # 自動フォールバックがオンの場合
                if self.email_config.get("auto_fallback", True):
                    self.email_config["simulation_mode"] = True
                    self._save_email_config()
                    self._show_notification("エラーのため、自動的にシミュレーションモードに切り替えました", ft.colors.AMBER)
                    
                    # シミュレーションモードで再試行
                    self._update_status("【シミュレーション】メール送信をシミュレート中...", ft.colors.ORANGE)
                    logger.info(f"【シミュレーション】メール通知をシミュレートしました: {gmail_address}")
                    self._show_notification(f"【シミュレーション】メール送信をシミュレートしました: {gmail_address}")
                    self._update_status("【シミュレーション】メール送信完了", ft.colors.GREEN)
                    
                    # 最終送信時刻を更新
                    if not is_test:
                        now = datetime.now(timezone.utc).astimezone(JST)
                        self.email_config["last_sent"] = now.isoformat()
                        self._save_email_config()
                
                # エラーの詳細情報をログに記録
                logger.error("メール送信詳細エラー:", exc_info=True)
            
            # 最終送信時刻を更新
            if not is_test:
                now = datetime.now(timezone.utc).astimezone(JST)
                self.email_config["last_sent"] = now.isoformat()
                self._save_email_config()
        
        except Exception as e:
            logger.error(f"メール通知処理に失敗しました: {e}", exc_info=True)
            if not is_test:
                self._show_notification(f"メール通知処理に失敗しました: {str(e)}")
                
                # 自動フォールバックがオンの場合
                if self.email_config.get("auto_fallback", True):
                    self.email_config["simulation_mode"] = True
                    self._save_email_config()
                    self._show_notification("エラーのため、自動的にシミュレーションモードに切り替えました", ft.colors.AMBER)
                    
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
        """設定タブのコンテンツを作成"""
        # シミュレーションモードスイッチの追加
        self.simulation_mode_switch = ft.Switch(
            label="シミュレーションモード（メール送信をシミュレートする）",
            value=self.email_config.get("simulation_mode", True),  # デフォルトでシミュレーションモードを有効に
            on_change=lambda e: self._toggle_simulation_mode(e)
        )
        
        # 自動フォールバックスイッチの追加
        self.auto_fallback_switch = ft.Switch(
            label="エラー時にシミュレーションモードに自動切り替え",
            value=self.email_config.get("auto_fallback", True),
            on_change=lambda e: self._toggle_auto_fallback(e)
        )
        
        # メール通知設定カード
        email_settings = ft.Card(
            content=ft.Container(
                content=ft.Column([
                    ft.Text("メール通知設定", weight=ft.FontWeight.BOLD),
                    self.email_enabled_switch,
                    self.simulation_mode_switch,
                    self.auto_fallback_switch,
                    ft.Text("Gmail設定（送受信兼用）", weight=ft.FontWeight.W_500, size=14),
                    self.gmail_address_field,
                    self.gmail_app_password_field,
                    ft.Row(
                        controls=[
                            self.email_save_button,
                            self.email_test_button
                        ]
                    ),
                    
                    # Gmail設定説明テキスト（コピー可能）
                    ft.Text("※以下の説明はコピーできます", size=12, color=ft.colors.GREEN),
                    
                    # SelectionAreaでテキストを選択可能にする
                    ft.SelectionArea(
                        content=ft.Column([
                            ft.TextField(
                                value="※メール送信には「Gmailアプリパスワード」が必要です\n"
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
                                "※エラーが続く場合は、新しいアプリパスワードを再生成してみてください",
                                multiline=True,
                                read_only=True,
                                min_lines=10,
                                max_lines=15,
                                text_size=12,
                                text_style=ft.TextStyle(color=ft.colors.BLACK),
                                border=ft.InputBorder.OUTLINE,
                                border_color=ft.colors.GREY_400,
                                bgcolor=ft.colors.GREY_100,
                                width=500,
                                border_radius=8,
                            )
                        ])
                    ),
                    
                    # コピーボタン
                    ft.ElevatedButton(
                        "説明をクリップボードにコピー",
                        icon=ft.icons.COPY,
                        on_click=self._copy_instruction_text
                    )
                ]),
                padding=20
            )
        )
        
        # すべての設定項目をリストビューに配置
        settings_items = [
            email_settings,
            ft.Text("※バッチ処理は1時間ごとに自動的に実行されます", 
                   color=ft.colors.GREY, italic=True),
            # 将来的に追加設定があればここに追加
            ft.Container(height=20),  # 余白を追加
        ]
        
        # スクロール可能なコンテナに配置
        return ft.Container(
            content=ft.ListView(
                controls=settings_items,
                spacing=10,
                padding=10,
                auto_scroll=True,
                expand=True,
            ),
            padding=10,
            expand=True
        )
        
    def _toggle_simulation_mode(self, e):
        """シミュレーションモードの切り替え"""
        self.email_config["simulation_mode"] = self.simulation_mode_switch.value
        self._save_email_config()
    
    def _toggle_auto_fallback(self, e):
        """自動フォールバックの切り替え"""
        self.email_config["auto_fallback"] = self.auto_fallback_switch.value
        self._save_email_config()
    
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