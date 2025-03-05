#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
UIコンポーネント関連の処理

このモジュールは、UI要素の作成や更新に関連する関数を提供します。
JobMonitorAppクラスから分離されたUI関連の処理が含まれています。

主な機能:
- カード生成関数
- 通知表示
- UIステータス更新
"""

import logging
import flet as ft
from typing import Dict, Any, Optional, Callable

# ロギングの設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_job_card(job: Dict[str, Any], 
                    format_date_func: Callable[[str], str], 
                    format_payment_func: Callable[[Dict[str, Any]], str],
                    open_url_func: Callable[[str], None]) -> ft.Card:
    """
    仕事情報からカードUIを作成
    
    Args:
        job: 仕事情報の辞書
        format_date_func: 日付フォーマット関数
        format_payment_func: 支払い情報フォーマット関数
        open_url_func: URL開く関数
        
    Returns:
        作成されたft.Cardオブジェクト
    """
    try:
        # 仕事情報から必要なデータを取得
        title = job.get('title', '（タイトル不明）')
        url = job.get('url', '')
        date = format_date_func(job.get('date', ''))
        payment_text = format_payment_func(job)
        
        # カード内のコンテンツを作成
        content = ft.Container(
            content=ft.Column([
                # タイトル行
                ft.Row([
                    ft.Icon(ft.icons.WORK_OUTLINE, color=ft.colors.BLUE),
                    ft.Text(
                        title,
                        weight=ft.FontWeight.BOLD,
                        size=16,
                        width=600,  # タイトルの最大幅を設定
                        overflow=ft.TextOverflow.ELLIPSIS,
                        max_lines=2,
                        selectable=True
                    ),
                ]),
                
                # 詳細情報行
                ft.Container(
                    content=ft.Row([
                        # 日付情報
                        ft.Row([
                            ft.Icon(ft.icons.CALENDAR_TODAY, color=ft.colors.GREEN, size=16),
                            ft.Text(date, size=14, color=ft.colors.GREY_700),
                        ]),
                        
                        # 報酬情報
                        ft.Row([
                            ft.Icon(ft.icons.ATTACH_MONEY, color=ft.colors.GREEN, size=16),
                            ft.Text(payment_text, size=14, color=ft.colors.GREY_700, selectable=True),
                        ]),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    margin=ft.margin.only(top=8, bottom=8),
                ),
                
                # ボタン行
                ft.Row([
                    ft.ElevatedButton(
                        "詳細を見る",
                        icon=ft.icons.OPEN_IN_NEW,
                        on_click=lambda e, url=url: open_url_func(url)
                    ),
                ], alignment=ft.MainAxisAlignment.END),
            ]),
            padding=15,
        )
        
        # カードを作成して返す
        return ft.Card(
            content=content,
            elevation=2,
            margin=ft.margin.only(bottom=10),
        )
        
    except Exception as e:
        logger.error(f"カード作成中にエラーが発生しました: {e}, job_id: {job.get('id', 'unknown')}")
        # エラーの場合はシンプルなエラーカードを返す
        error_card = ft.Card(
            content=ft.Container(
                content=ft.Text(f"カードの表示に失敗しました: {str(e)}", color=ft.colors.RED),
                padding=15
            ),
            margin=ft.margin.only(bottom=10)
        )
        return error_card

def show_notification(page: ft.Page, message: str, color=None):
    """
    通知を表示
    
    Args:
        page: fletページオブジェクト
        message: 表示するメッセージ
        color: メッセージの色
    """
    try:
        # 最新のFlet推奨方法でSnackBarを表示する
        snack = ft.SnackBar(
            content=ft.Text(message, color=color),
            action="閉じる",
            open=True
        )
        # overlayに追加して表示
        if hasattr(page, "overlay") and page.overlay is not None:
            page.overlay.append(snack)
            page.update()
        else:
            # 旧式の方法でフォールバック
            page.snack_bar = snack
            page.snack_bar.open = True
            page.update()
    except Exception as e:
        logger.error(f"通知の表示に失敗しました: {e}")
        # 通知の表示に失敗しても、アプリは続行する

def update_status(status_text: ft.Text, message: str, color=ft.colors.GREEN, page: Optional[ft.Page] = None):
    """
    ステータスメッセージを更新
    
    Args:
        status_text: 更新するテキストオブジェクト
        message: 表示するメッセージ
        color: メッセージの色
        page: 更新するページ (Noneの場合は更新しない)
    """
    status_text.value = message
    status_text.color = color
    if page:
        page.update()

def create_settings_tab(email_enabled_switch: ft.Switch,
                      simulation_mode_switch: ft.Switch,
                      auto_fallback_switch: ft.Switch,
                      gmail_address_field: ft.TextField,
                      gmail_app_password_field: ft.TextField,
                      email_save_button: ft.ElevatedButton,
                      email_test_button: ft.ElevatedButton,
                      copy_instruction_func: Callable) -> ft.Container:
    """
    設定タブを作成
    
    Args:
        email_enabled_switch: メール有効スイッチ
        simulation_mode_switch: シミュレーションモードスイッチ
        auto_fallback_switch: 自動フォールバックスイッチ
        gmail_address_field: Gmailアドレスフィールド
        gmail_app_password_field: Gmailアプリパスワードフィールド
        email_save_button: メール設定保存ボタン
        email_test_button: テストメール送信ボタン
        copy_instruction_func: 説明コピー関数
        
    Returns:
        設定タブのコンテナ
    """
    return ft.Container(
        content=ft.Column([
            # メール設定セクション
            ft.Text("メール通知設定", weight=ft.FontWeight.BOLD, size=20),
            ft.Divider(),
            
            ft.Container(
                content=ft.Column([
                    email_enabled_switch,
                    simulation_mode_switch,
                    auto_fallback_switch,
                    
                    ft.Text("Gmail設定（送受信兼用）", weight=ft.FontWeight.W_500, size=16),
                    gmail_address_field,
                    gmail_app_password_field,
                    
                    ft.Row([
                        email_save_button,
                        email_test_button
                    ]),
                    
                    # Gmailアプリパスワードの説明
                    ft.Text("※以下の説明はコピーできます", size=12, color=ft.colors.GREEN),
                    
                    # SelectionAreaでテキストを選択可能にする
                    ft.SelectionArea(
                        content=ft.TextField(
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
                            min_lines=12,
                            max_lines=15,
                            text_size=12,
                            border=ft.InputBorder.OUTLINE,
                            border_color=ft.colors.GREY_400,
                            bgcolor=ft.colors.GREY_100,
                        )
                    ),
                    
                    # コピーボタン
                    ft.ElevatedButton(
                        "説明をクリップボードにコピー",
                        icon=ft.icons.COPY,
                        on_click=copy_instruction_func
                    )
                ]),
                padding=20
            ),
            
            ft.Divider(),
            
            # 利用規約セクション
            ft.Text("使用条件", weight=ft.FontWeight.BOLD, size=20),
            ft.Container(
                content=ft.Column([
                    ft.Text(
                        "このアプリケーションは個人的な学習目的で作成されたもので、商用利用は想定されていません。\n"
                        "クラウドワークスの利用規約に従い、適切な方法でのみご利用ください。\n"
                        "本アプリは公式のものではなく、予告なく動作しなくなる可能性があります。",
                        size=14,
                        color=ft.colors.GREY_800
                    ),
                ]),
                padding=20
            )
        ]),
        padding=20
    ) 