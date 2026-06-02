# 売掛金 可視化・債権回収管理アプリ

MF会計の売掛金補助元帳を取り込み、**売掛残高の見える化**と**債権回収（督促）管理**を行う社内向けアプリ。
会計記帳の本体は MF会計。本アプリは「MFに無い売掛の可視化と督促運用」を担う（詳細は `docs/db_design.md`）。

> ⚠ これは開発・検証段階です。本番構成（クラウドPostgres等）は別途設計中（`docs/db_design.md` §9）。

## 主な機能

- ダッシュボード（総売掛残高・エイジング・要フォロー先）
- 顧問先一覧（残高・延滞順の回収ワークリスト）
- 顧問先詳細（得意先元帳／未入金請求／督促履歴・記録）
- 督促ワークリスト・消込（FIFO自動＋手動）・月次残高確定/CSV出力・取込状況

## セットアップ（各自のPC）

前提: Python 3.12 以降。

```bash
# 1. クローン
git clone https://github.com/lucaslucas7010301/accounts-receivable.git
cd accounts-receivable

# 2. 仮想環境を作成して依存をインストール
python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt

# 3. DB作成（マイグレーション）
./venv/bin/python manage.py migrate

# 4. サンプルデータを投入（ダミーの顧問先・請求・入金・督促）
./venv/bin/python manage.py seed_demo

# 5. 起動
./venv/bin/python manage.py runserver
```

→ http://127.0.0.1:8000/ にアクセス。ログイン: **admin / admin**

## 実データ（MF補助元帳CSV）の取り込み

MF会計の「売掛金 補助元帳」CSV（CP932）を取り込めます。

```bash
./venv/bin/python manage.py import_mf_ledger "/path/to/補助元帳.csv"
```

または起動後に画面の **「取込」** からCSVをアップロード。
取込仕様（借方→請求／貸方→入金、繰越補完、期日ルール、非顧問先除外、残高照合）は `docs/db_design.md` §6.1 を参照。

> 🔒 **個人情報・機密の注意**: 実データのCSVと `db.sqlite3`（取込後のDB）には実在の顧問先名・金額が含まれます。
> これらは `.gitignore` で除外しており **リポジトリにコミットしないでください**。動作確認は `seed_demo` のダミーデータで行うのが基本です。

## 技術構成

- Django 5.2 / SQLite（開発）/ django-simple-history（変更履歴）
- 主要モジュール: `accounting/models.py`（データモデル）, `services.py`（残高・FIFO消込）,
  `importers.py`（MF取込）, `views.py`（画面）

## ドキュメント

- `docs/db_design.md` — データモデル・取込仕様・本番設計
- `docs/screen_design.md` — 画面設計
