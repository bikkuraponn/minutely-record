"""
comment_count2/thread_count/comment_account の Turso版計算。

以前は supabase_record/comment_count.py が YouTube commentThreads.list を毎時
直接ライブクロールして計算していたが、comment_sync が維持している Turso
`comments` テーブルには同じ生データが既に入っているため、そちらから計算する
(flaskr/turso_stats.py が同じ理由でライブ読み取りを Turso 読みに置き換えた
パターンと同じ)。YouTube APIを一切消費しない。

定義は supabase_record/comment_count.py と同一:
  「直近 hours 時間以内のスレッド＋その全返信」
  +「hours〜extended_hours 時間前のスレッドへの、直近 hours 時間以内の返信のみ」
  （スレッド自体は対象外）

削除済み(is_deleted=1)は元実装のライブクロールが実質的に見ない状態と同じに
揃えるため除外する。

「直近1時間以内のスレッドの返信は、返信の投稿時刻を問わず全部数える」のは
手抜きではない: 返信は必ず親スレッドの投稿より後に発生するため、親スレッドが
直近1時間以内なら、その返信も自動的に直近1時間以内になる(時系列上ありえない
順序を除けば矛盾しない)。よって「全返信」と「直近1時間以内の返信」は
この場合常に同じ集合になる。

【固定コメント対策について(2026-07-28)】
元の supabase_record/comment_count.py には `if "[REDACTED_PINNED_COMMENT_TEXT]" not in text:` という
説明コメント無しの文字列マッチがあった。これは「スパム除外」ではなく、実際には
この動画の固定コメント本文そのものを直接ハードコードした固定コメント除外策
だった(動画投稿者に確認して判明。詳細は AGENTS.md の Non-obvious Implementation
Details 参照)。YouTube API の commentThreads.list(order="time") はページネーション
順として固定コメントを常に先頭に返す(実際の投稿時刻に関係なく)ため、ライブ
クロールではこれを識別する手段がAPI上に無く、既知の固定コメント文言との
文字列一致という場当たり的な方法に頼っていた。

このTurso版はライブAPIのページネーション順ではなく Turso `comments.published_at`
(UPSERT時も上書きされない、投稿時点の実際の値)を直接 `WHERE published_at >= ?`
で絞り込むため、「常に先頭に来る」という問題自体が発生しない。固定コメントの
実際の投稿時刻が直近1〜3時間の窓に入らない限り、特別な対策なしで自動的に
対象外になる。そのため文字列マッチのハックはここでは意図的に移植していない。
唯一の例外は「直近1時間以内に新しくピン留めされたコメント」だが、これは
実質的に新規コメントそのものなのでカウントされて問題ない。
"""

IN_CHUNK = 200  # Turso IN() 節のチャンクサイズ(comment_syncのRECHECK_IN_CHUNKと同じ考え方)


def _chunked_in_query(turso_client, sql_template: str, ids: list[str], extra_args: list | None = None):
    """ids を IN_CHUNK 件ずつに分けて sql_template (id用の1個の '?' を含む) を実行し、行を結合して返す。"""
    rows = []
    for i in range(0, len(ids), IN_CHUNK):
        chunk = ids[i:i + IN_CHUNK]
        placeholders = ",".join("?" for _ in chunk)
        sql = sql_template.format(placeholders=placeholders)
        args = list(chunk) + (extra_args or [])
        rows.extend(turso_client.query(sql, args))
    return rows


def get_recent_comment_counts(turso_client, now_epoch: int, hours: int = 1, extended_hours: int = 3) -> dict:
    """戻り値: {"account": {handle: count}, "thread_count": int, "count": int}
    (supabase_record/comment_count.py の get_recent_comment_counts と同じ形)
    """
    cutoff_recent = now_epoch - hours * 3600
    cutoff_extended = now_epoch - extended_hours * 3600

    account: dict[str, int] = {}
    count = 0
    thread_count = 0

    def _add(handle: str | None, n: int = 1) -> None:
        nonlocal count
        if handle:
            account[handle] = account.get(handle, 0) + n
        count += n

    # --- 直近 hours 時間以内のスレッド ---
    # 固定コメント対策の文字列マッチは意図的に移植していない
    # (published_at で絞り込む時点で固定コメントは自然に対象外になるため。
    #  モジュールdocstring参照)。
    fresh_threads = turso_client.query(
        "SELECT comment_id, handle FROM comments "
        "WHERE parent_id IS NULL AND published_at >= ? AND is_deleted = 0",
        [cutoff_recent],
    )
    fresh_thread_ids = [t["comment_id"] for t in fresh_threads]

    for t in fresh_threads:
        thread_count += 1
        _add(t.get("handle"))

    # 直近スレッドの返信は投稿時刻を問わず全部(モジュールdocstring参照)。
    if fresh_thread_ids:
        fresh_replies = _chunked_in_query(
            turso_client,
            "SELECT parent_id, handle FROM comments "
            "WHERE parent_id IN ({placeholders}) AND is_deleted = 0",
            fresh_thread_ids,
        )
        for r in fresh_replies:
            _add(r.get("handle"))

    # --- hours〜extended_hours 時間前のスレッド: スレッド自体は対象外、
    #     直近 hours 時間以内の返信のみカウント ---
    older_threads = turso_client.query(
        "SELECT comment_id FROM comments "
        "WHERE parent_id IS NULL AND published_at >= ? AND published_at < ? AND is_deleted = 0",
        [cutoff_extended, cutoff_recent],
    )
    older_thread_ids = [t["comment_id"] for t in older_threads]

    if older_thread_ids:
        older_replies = _chunked_in_query(
            turso_client,
            "SELECT parent_id, handle FROM comments "
            "WHERE parent_id IN ({placeholders}) AND is_deleted = 0 AND published_at >= ?",
            older_thread_ids,
            extra_args=[cutoff_recent],
        )
        for r in older_replies:
            _add(r.get("handle"))

    return {"account": account, "thread_count": thread_count, "count": count}
