# 設計書 — 売掛金 可視化・債権回収管理アプリ

> 対象: `accounts-receivable`（Django 5.2）
> 役割: **売掛金の可視化＋債権回収（督促）管理レイヤー**。会計記帳の本体は **MF会計**。
> 利用者: 5名（非技術・経理中心） / クラウド（物理サーバーなし）

---

## 1. 背景と現状（As-Is）

```
  board ──(請求/売上 CSVを手動エクスポート→MFへ手動インポート)──> MF会計
                                                                    │
  銀行預金データ(MF自動連携) / NSS引落結果 ──(入金として登録)────────┘
                                                                    │
  月末+5日: MF会計の残高を手作業でスプレッドシートに転記 → 報告       │
                                                                    │
  回収必要性が高い先 → 担当者が顧問先へ連絡 → 共有スプレッドシートを更新
```

- **会計の記帳本体は MF会計**（決算・申告もMFで社内完結）。
- **board** = 売上/請求の発生源。**MF＋銀行＋NSS** = 入金の発生源。
- 売掛残高の把握・督促の記録は**スプレッドシートの手作業**。

### 解消したい課題

| # | 課題 | 本アプリで解決する機能 |
|---|------|----------------------|
| 課題1 | board→MFの登録を自動化したい | （将来）board取込→MF自動起票。当面は board取込でアプリ内可視化 |
| 課題2 | リアルタイムに売掛残高を把握したい | board(請求)−入金 を常時集計するダッシュボード |
| 課題3 | **いつ誰がどうアクションしたかを明確にしたい** | **督促アクション記録（担当者・日時・手段・結果）＋変更履歴** |
| 課題4 | MF→スプレッドシート変換が重い | 月次報告レポートの自動出力 |

---

## 2. スコープと方針

**このアプリは「もう一つの仕訳台帳」を作らない。** 複式簿記の記帳はMF会計に任せ、MFに無い「売掛の見える化」と「督促運用の記録」を埋める特化ツールとする。

| 原則 | 内容 |
|------|------|
| P1 | **MFが会計の真実。アプリは売掛"管理"の真実**（残高は board − 入金 で算出） |
| P2 | **二重記帳しない**（売上高/現預金などの相手科目は持たない） |
| P3 | **全データに操作者・時刻、全変更を履歴化**（課題3） |
| P4 | **取込は冪等**（同じデータを何度入れても重複しない） |
| P5 | **法的帳簿ではない**ため、重い不変性対策（DBトリガ・権限剥奪・電帳法対応）は**不要**。監査はアプリ層の履歴で担保 |

### フェーズ計画

- **Phase 1（今回）**: 可視化（残高・エイジング・ダッシュボード）＋ 督促管理 ＋ 月次レポート出力。連携は **定期エクスポート/インポートの自動化**（board・MF/銀行/NSS → アプリ）。
- **Phase 2（将来）**: board → アプリ → **MFへ仕訳を自動連携**（API化）、よりリアルタイムな取込。

---

## 3. データモデル（ER）

```
┌──────────┐ created_by/actor(全テーブル)
│   User   │───────────────────────────────────┐
│ (担当者) │                                    │
└──────────┘                                    │
                                                ▼
        ┌───────────────────────────────────────────────┐
        │                  Client (顧問先)               │
        │           code / name / 担当者 / 締め日 / サイト │
        └───┬───────────────┬───────────────┬────────────┘
            │1              │1              │1
            │*              │*              │*
   ┌────────▼──────┐ ┌──────▼───────┐ ┌─────▼──────────────┐
   │   Invoice     │ │   Receipt    │ │ CollectionAction   │
   │ 請求/売上     │ │   入金       │ │ 督促アクション     │
   │ (board由来)   │ │(MF/銀行/NSS) │ │ ★監査の主役        │
   └───────┬───────┘ └──────┬───────┘ └────────────────────┘
           │                │
           └──FK──┐  ┌──FK──┘
                  ▼  ▼
            ┌──────────────┐        ┌──────────────────┐
            │ ImportBatch  │        │  MonthlyClose    │
            │ 取込バッチ   │        │  月次残高確定    │
            │ (連携の監査) │        │ + CloseLine(明細)│
            └──────────────┘        └──────────────────┘

AR残高(顧問先別) = Σ Invoice.amount − Σ Receipt.amount   ← 算出値（テーブルに持たない）
請求ごとの未納     = Invoice.amount − Σ Allocation.amount   ← どの債権が残っているか
   Allocation(消込) が Invoice ↔ Receipt を引当（FIFO自動 / 手動）
自動生成: historical_client / historical_invoice / historical_receipt /
          historical_collectionaction / historical_allocation （simple-history）
```

---

## 4. テーブル定義

### 4.0 共通基底

```python
from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

class Audited(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+', verbose_name='登録者')
    created_at = models.DateTimeField('登録日時', auto_now_add=True)
    updated_at = models.DateTimeField('更新日時', auto_now=True)
    class Meta:
        abstract = True
```

### 4.1 Client（顧問先 / 得意先）

| カラム | 型 | 備考 |
|--------|----|------|
| code | varchar(20) UNIQUE | board等との突合キー |
| name | varchar(100) | |
| is_active | bool | 論理無効化（削除しない） |
| assignee | FK→User, null可 | **担当者**（督促の責任者） |
| closing_day | smallint, null可 | 締め日（回収サイト計算用） |
| payment_terms_days | smallint, null可 | 入金サイト（日） |

### 4.2 Invoice（請求/売上 — board由来）

| カラム | 型 | 備考 |
|--------|----|------|
| client | FK→Client PROTECT | |
| board_invoice_id | varchar UNIQUE | **冪等キー**（重複取込防止） |
| billing_date | date | 請求日 |
| due_date | date | 入金期日（エイジング判定の基準） |
| amount | decimal(12,0) | 円・税込（boardの請求総額） |
| description | varchar(200) | |
| source | varchar(10) default 'board' | |
| import_batch | FK→ImportBatch, null可 | どの取込で入ったか |

### 4.3 Receipt（入金 — MF/銀行/NSS由来）

| カラム | 型 | 備考 |
|--------|----|------|
| client | FK→Client PROTECT | |
| external_id | varchar UNIQUE, null可 | **冪等キー**（MF/NSSの明細ID） |
| receipt_date | date | 入金日 |
| amount | decimal(12,0) | |
| source | varchar(12) | mf_bank / nss / manual |
| description | varchar(200) | |
| import_batch | FK→ImportBatch, null可 | |

> 入金↔請求の引当は **Allocation テーブル**（§4.7）で管理する。通常は **FIFO自動引当**、例外（係争・指定入金）は**手動で付け替え**。これにより「どの請求がいくら未納か」を正確に追える。

### 4.4 CollectionAction（督促アクション）★課題3の中核

| カラム | 型 | 備考 |
|--------|----|------|
| client | FK→Client PROTECT | |
| acted_at | datetime | **いつ** |
| actor | FK→User PROTECT | **誰が** |
| method | varchar(10) | call / email / visit / letter / other（**どう**） |
| content | text | 連絡内容 |
| result | text | 結果・先方回答 |
| status | varchar(12) | open / in_progress / resolved |
| next_action_date | date, null可 | 次回フォロー予定 |

- このテーブル自体が「いつ・誰が・どうアクションしたか」の記録。さらに `HistoricalRecords()` で**事後修正も追跡**。
- 共有スプレッドシートの督促管理を置き換える。

### 4.5 ImportBatch（取込バッチ — 連携の監査）

| カラム | 型 | 備考 |
|--------|----|------|
| source | varchar(10) | board / mf / nss |
| source_ref | varchar(200) | ファイル名・期間など |
| row_total / row_imported / row_skipped / row_error | int | 件数 |
| status | varchar(10) | success / partial / failed |
| log | text | スキップ・エラー理由 |

- 自動取込が「いつ・何件・成否」を残す。冪等取込のトラブルシュートと監査に使用。

### 4.6 MonthlyClose / MonthlyCloseLine（月次残高確定）

| MonthlyClose | 型 | 備考 |
|--------------|----|------|
| period | varchar(7) UNIQUE | 'YYYY-MM' |
| closed_by | FK→User | 確定者 |
| note | text | |

| MonthlyCloseLine | 型 | 備考 |
|------------------|----|------|
| close | FK→MonthlyClose | |
| client | FK→Client | |
| balance | decimal(12,0) | 確定時点の顧問先別残高（スナップショット） |

- 「月末+5日に残高確定して報告」の運用を、**確定スナップショット＋レポート出力**で置き換える。MFとの月次照合の証跡にもなる。

### 4.7 Allocation（消込 — 入金↔請求の引当）

「いつの債権が残っているか」を正確に追うための、入金と請求の引当テーブル。

| カラム | 型 | 備考 |
|--------|----|------|
| receipt | FK→Receipt PROTECT, related_name='allocations' | 引当元の入金 |
| invoice | FK→Invoice PROTECT, related_name='allocations' | 引当先の請求 |
| amount | decimal(12,0) | この引当額 |
| method | varchar(10) | **fifo_auto**（自動） / **manual**（手動） |

- **請求残額** = `Invoice.amount − Σ allocations.amount`（0超なら未納）。
- **入金未引当額** = `Receipt.amount − Σ allocations.amount`（前受・過入金で残る）。
- **FIFO自動引当**: 取込後にバッチが顧問先ごとに「未引当の入金」を「古い未納請求」から順に引き当て、`method='fifo_auto'` の Allocation を作成。再実行時は **fifo_auto 分のみ作り直し（冪等）**、`manual` は保護して上書きしない。
- **手動修正**: 係争・指定入金などは人が `manual` 引当を作成/修正。`HistoricalRecords()` で「誰がいつ付け替えたか」も残る。
- 制約: Σ(receipt引当) ≤ receipt.amount、Σ(invoice引当) ≤ invoice.amount（過引当防止）。

---

## 5. 残高・エイジング・ダッシュボード

```python
from django.db.models import Sum

def ar_balance(client, as_of=None):
    """顧問先別の売掛残高 = Σ請求 − Σ入金（as_of 時点）"""
    inv = Invoice.objects.filter(client=client)
    rcp = Receipt.objects.filter(client=client)
    if as_of:
        inv = inv.filter(billing_date__lte=as_of)
        rcp = rcp.filter(receipt_date__lte=as_of)
    debit  = inv.aggregate(s=Sum('amount'))['s'] or 0
    credit = rcp.aggregate(s=Sum('amount'))['s'] or 0
    return debit - credit
```

- **ダッシュボード**: 総売掛残高、顧問先別残高、期日超過（エイジング: 0-30/31-60/61-90/90+日）、直近の督促アクション、要フォロー先（`next_action_date` 到来）。
- **エイジング**: 未消込の請求を `due_date` 基準でバケット集計。
- **月次レポート出力**: 顧問先別残高・エイジング・督促状況を CSV/Excel で出力 → 課題4（手作業転記）を解消。

### 5.1 売掛金元帳ビュー（総勘定元帳のような残高推移）

`Invoice`（増加＝借方相当）と `Receipt`（減少＝貸方相当）を**日付順に統合し、残高を累計**して表示する。
**主目的は「顧問先ごとの残高推移（得意先元帳）」**＝顧問先で絞り込んだビュー。「この顧問先はいつ請求し、いつ入金があり、今残高いくらか」を時系列で追える。顧問先で絞らなければ総勘定元帳（売掛金）相当にもなる（同一ロジック）。

| 列 | 出所 |
|----|------|
| 日付 | Invoice.billing_date / Receipt.receipt_date |
| 区分 | 請求 / 入金 |
| 摘要 | description |
| 増加(+) | Invoice.amount |
| 減少(−) | Receipt.amount |
| 残高 | 累計（増加 − 減少） |
| ソース | board_invoice_id / Receipt.source（MF/NSS） |
| 登録者 | created_by |

```python
def ar_ledger(client=None, date_from=None, date_to=None):
    """売掛金元帳: 請求(+)・入金(−)を時系列で並べ残高を累計して返す"""
    inv = Invoice.objects.all()
    rcp = Receipt.objects.all()
    if client:
        inv, rcp = inv.filter(client=client), rcp.filter(client=client)
    rows = (
        [{'date': i.billing_date, 'kind': '請求', 'plus': i.amount, 'minus': 0,
          'desc': i.description, 'src': i.board_invoice_id, 'by': i.created_by} for i in inv]
        + [{'date': r.receipt_date, 'kind': '入金', 'plus': 0, 'minus': r.amount,
            'desc': r.description, 'src': r.get_source_display(), 'by': r.created_by} for r in rcp]
    )
    rows.sort(key=lambda x: x['date'])
    if date_from: rows = [x for x in rows if x['date'] >= date_from]
    if date_to:   rows = [x for x in rows if x['date'] <= date_to]
    bal = 0
    for x in rows:
        bal += x['plus'] - x['minus']
        x['balance'] = bal
    return rows
```

- 期間絞り込み時は、期首残高（date_from 以前の累計）を別途算出して繰越残高として頭に表示する。
- 旧アプリの `sub_ledger` の残高累計ロジックと同型（データ源を仕訳→Invoice/Receiptに置換）。
- **任意拡張**: 同じタイムラインに `CollectionAction`（督促）を差し込み、請求・入金・督促を1本の「顧問先タイムライン」として表示すると回収業務に有用。
- 注: これは**残高推移の元帳（業務ビュー）**。レコードの作成・修正・取消の証跡は §7 の `simple-history`（変更履歴）が担い、両者は別物。

### 5.2 未入金請求一覧・エイジング（いつの債権が残っているか）

`Allocation`（§4.7）の引当結果から、**請求ごとの残額**を算出して未納を特定する。

| 列 | 出所 |
|----|------|
| 請求日 / 期日 | Invoice.billing_date / due_date |
| 請求額 | Invoice.amount |
| 残額 | amount − Σ Allocation.amount（0超＝未納） |
| 経過日数 | 基準日 − due_date |
| エイジング区分 | 0-30 / 31-60 / 61-90 / 90+ 日 |

- 顧問先別 ×（請求月＝ヴィンテージ）で「**どの時点の債権がいくら残っているか**」を表示。FIFO自動引当がベース、手動修正が反映される。
- **整合性**: 顧問先残高 = Σ(請求残額) − Σ(入金未引当)。未引当入金（前受・過入金）があれば差分として明示する。
- ダッシュボード・督促一覧から、未納請求と経過日数を見て回収アクション（`CollectionAction`）に繋げる。

---

## 6. 連携設計（定期エクスポート/インポートの自動化）

Phase 1 は **スケジュール実行のバッチ**（Django管理コマンド + cron / プラットフォームのスケジューラ）で取り込む。

```
[定期ジョブ・日次想定]
  board   → 請求CSVを自動取得 → upsert Invoice (board_invoice_id で冪等)
  MF/銀行 → 入金CSVを自動取得 → upsert Receipt (external_id で冪等)
  NSS     → 引落結果を自動取得 → upsert Receipt
  → 各実行を ImportBatch に記録（件数・成否・スキップ理由）
```

- **冪等性が肝**: `board_invoice_id` / `external_id` の UNIQUE 制約 + `update_or_create` で、同じデータを何度取り込んでも重複しない。
- 「リアルタイム」は厳密な即時ではなく **自動同期による"手間ゼロで最新"**（日次〜数時間間隔）。手作業の転記が消えることが本質。
- 金額・日付・顧問先コードの検証はバッチ内で行い、不整合行は `ImportBatch.log` に記録してスキップ（既存 `_parse_bulk_rows` のバリデーションを流用可能）。
- Phase 2 で board API / MF クラウド API に置換し、MFへの仕訳自動起票を追加。

### 6.1 MF会計 売掛金補助元帳CSVの取込仕様（実装済み）

現状の主データは **MF会計の売掛金 補助元帳エクスポート（CP932/Shift-JIS）**。
取込は `python manage.py import_mf_ledger <csv>`（または取込状況画面のCSVアップロード）で実行。

**1行＝1顧問先の売掛金の増減1件**として、次のように対応付ける。

| CSV列 | 取り込み先 |
|-------|-----------|
| 補助科目「コード 名称」 | Client（コード・名称） |
| 借方金額 > 0 | **Invoice（請求/売上）** billing_date=取引日, amount=借方金額 |
| 貸方金額 > 0 | **Receipt（入金/減額）** receipt_date=取引日, amount=貸方金額 |
| 相手勘定科目/相手補助科目 | Invoice/Receipt の摘要に保持、Receipt.source の判定に使用 |
| 残高 | 取込しない（算出値で再現し、照合に使用） |

**取込ルール**

- **顧問先の判定**: 補助科目が「コード␣名称」形式の行のみ取り込む。コードを持たない行（`NSS`＝集金代行の中継勘定、`決算`、`六番サービス`等の内部勘定）は**除外**。
- **繰越残高**: CSV先頭行の `残高 − 借方 + 貸方` を期首残高とし、>0なら繰越Invoice（計上日=期間先頭の前日）、<0なら前受Receiptを補完。これによりMFの累計残高（期間前の履歴を含む）と一致させる。
- **入金期日（エイジング用、MF元帳に無いため補完）**:
  - 顧問料（摘要/相手補助科目に「顧問料」）→ **当月末**（当月請求・当月回収）
  - その他 → **翌月末**（月末締め翌月末払い）
- **Receipt.source**: 相手補助科目に「NSS」→ `nss`／相手勘定科目に「預金・現金・銀行」→ `mf_bank`／それ以外→ `manual`。
- **冪等性**: 既定で全取引データを置換（`--keep` で追記）。取引Noが非ユニークのため行ごとに連番IDを採番。将来の定期取込では行ハッシュ等で冪等化する。
- **残高照合**: 取込後、顧問先ごとに「算出残高 = Σ請求 − Σ入金」がCSVの最終残高と一致するか検証し、`ImportBatch` に結果を記録（不一致は要調査としてログ）。

> 注: board連携（Phase 2）が入ると、請求は board が正本・入金は MF/銀行が正本になる。その際は本取込は入金側中心に移行する。

---

## 7. 監査設計（課題3）— アプリ層で十分

法的帳簿ではないため、DBトリガや権限剥奪・電帳法対応は**不要**（それはMFの役割）。本アプリの監査は次の2点で担保：

1. **`created_by` / `actor` / `created_at` を全データに付与**（要・Django標準認証＋全ビュー `@login_required`、5名分のユーザー）。
2. **`django-simple-history`** で Client / Invoice / Receipt / CollectionAction の全変更（前後の値・実行者・時刻）を `historical_*` に自動記録。
   - 自動取込の実行者は **システムユーザー**＋ `ImportBatch` で追跡。

これで「いつ・誰が・どの顧問先に・どうアクションし・データをどう直したか」が完全に追える。

---

## 8. 制約・インデックス

| テーブル | インデックス / 制約 | 目的 |
|----------|--------------------|------|
| Invoice | `board_invoice_id` UNIQUE | 冪等取込 |
| Invoice | `(client, billing_date)`, `(client, due_date)` | 残高・エイジング集計 |
| Receipt | `external_id` UNIQUE | 冪等取込 |
| Receipt | `(client, receipt_date)` | 残高集計 |
| CollectionAction | `(client, acted_at)`, `(status, next_action_date)`, `(actor)` | 督促一覧・要フォロー抽出 |
| Client / amount系 | `code` UNIQUE / CHECK `amount > 0` | 一意性・負値防止 |

---

## 9. ホスティング（非技術運用向け）

- **turnkey マネージドPostgres**（Render Postgres / Supabase Pro 等、常時稼働プラン）を推奨。
- **「一度構築したら触らない」**設計: 初期構築（DB作成・接続・自動バックアップ・スケジューラ）は技術者がセットアップ時に一回だけ。日々の利用はブラウザのみ。
- バックアップはプラットフォームの自動バックアップ＋PITRで足りる（法的7年保管の重い要件はMF側が担うため、本アプリは運用データの保全が目的）。
- DBは公開しない・接続情報は環境変数/シークレット管理。

---

## 10. 現行プロトタイプからの主な変更

| 現行（複式簿記台帳） | 改訂（売掛管理レイヤー） |
|----------------------|--------------------------|
| `Account`（勘定科目） | **削除**（MFが持つ。相手科目不要） |
| `JournalEntry`（借方/貸方） | **Invoice + Receipt** に分離（売掛の片側だけ） |
| `Transaction` + `post()` | **board/入金の取込バッチ**に置換 |
| `group_id` / Voucher構想 | **不要**（二重記帳しないため） |
| 残高 = 仕訳の累計 | 残高 = Σ請求 − Σ入金（算出） |
| （督促管理なし） | **CollectionAction を新設**（課題3の中核） |
| 手動CSV一括登録 | **定期自動取込（ImportBatch）** |

> 現行コードはプロトタイプ段階（本番はMF会計で運用中）のため、アプリDBの移行データは無い。上記モデルで作り直す。

---

## 11. 残課題・将来拡張

- **Phase 2: MFへの仕訳自動起票**（board API / MF クラウド API、課題1の根本解決）。
- **督促の自動化**（期日超過の自動アラート、テンプレメール送信）。
- **board/MF/NSS の具体的なエクスポート形式・API提供状況の調査**（次アクション）。
- 通知連携（Slack等への要フォロー通知）。
