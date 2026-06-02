# ONBOARDING — 売掛金 可視化・債権回収管理アプリ

開発チーム向けオンボーディング。このリポジトリで何をしているか・どう動かすか・どこを触るかを最短で把握するためのガイド。

## このアプリは何か

MF会計の**売掛金 補助元帳**を取り込み、**売掛残高の見える化**と**債権回収（督促）管理**を行う社内向けDjangoアプリ。

重要な前提（誤解しやすい点）:
- **会計記帳の本体は MF会計**。このアプリは「複式簿記の台帳」ではない。
- 残高は仕訳から積むのではなく **Σ請求(Invoice) − Σ入金(Receipt)** の算出値。
- 売上/請求の発生源は board、入金は MF/銀行/NSS。当面は MF補助元帳CSVを取り込む。
- 監査は「いつ誰がどんな督促・取込をしたか」の追跡が主眼（法的帳簿はMF側）。

詳細は `docs/db_design.md`（データモデル・取込仕様・本番設計）と `docs/screen_design.md`（画面設計）。

## まず動かす

前提: Python 3.12+。

```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py seed_demo        # ダミーデータ投入
./venv/bin/python manage.py runserver        # http://127.0.0.1:8000/  (admin / admin)
```

実データ（MF補助元帳CSV・CP932）の取込:
```bash
./venv/bin/python manage.py import_mf_ledger "/path/to/補助元帳.csv"
```
または起動後に画面の「取込」からアップロード。

## コードの歩き方（accounting/）

| ファイル | 役割 |
|----------|------|
| `models.py` | データモデル: Client / Invoice / Receipt / Allocation(消込) / CollectionAction(督促) / ImportBatch / MonthlyClose |
| `services.py` | 残高計算・得意先元帳・エイジング・**FIFO自動消込** `reconcile_fifo()` |
| `importers.py` | MF補助元帳CSVの取込ロジック（コマンドと画面アップロードで共用） |
| `views.py` | 画面: dashboard / client_list / client_detail / followups / allocation / monthly_close / import_status |
| `templates/accounting/` | 各画面のHTML（`base.html` がレイアウト・ナビ） |
| `management/commands/` | `seed_demo`（ダミー）, `import_mf_ledger`（実データ取込） |

## 重要な概念

- **消込（Allocation）**: 入金↔請求の引当。通常は FIFO自動（古い請求から充当）、係争・指定入金は `method='manual'` で手動付替（自動再計算で保護される）。「いつの債権が残っているか」を支える。
- **繰越残高**: 取込CSVは途中期間からのため、先頭行の残高から繰越を補完してMFの累計残高と一致させている。
- **残高照合**: 取込後、全顧問先で「算出残高＝CSV最終残高」を検証し ImportBatch に記録。

## よくある作業の入口

- 画面を増やす → `views.py` にビュー追加 → `urls.py` にルート → `templates/accounting/` にHTML → `base.html` のナビに追加。
- 取込ルールの調整 → `importers.py`（期日ルール `_due_date`、除外条件、source判定など）。
- 残高/エイジングのロジック → `services.py`。

## 注意（ハマりどころ）

- 🔒 **実データはGitに乗せない**: `db.sqlite3`（取込後DB）と実CSVには実顧問先名・金額が含まれる。`.gitignore`済み。動作確認は `seed_demo` のダミーで。
- 顧問先ページのURLは **顧問先コード**基準（`/clients/<code>/`）。再取込で内部IDが変わってもURLは壊れない。
- 取込の「置換」は**請求・入金・引当のみ**削除。顧問先・督促・月次確定は保持する。
- これは開発段階。本番（クラウドPostgres・自動バックアップ・認証強化）は `docs/db_design.md` §9 で設計中。

## 困ったら

- 仕様の意図は `docs/` の設計書に背景込みで書いてある。まずそこを読むと早い。
