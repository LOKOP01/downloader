package com.xd.vdl.core.manga

/** 书架排序方式（与桌面端「按名称 / 按进度 / 最近阅读」对齐，多了「加入时间」） */
enum class MangaSort(val label: String) {
    RECENT("最近阅读"),
    ADDED("加入时间"),
    TITLE("标题"),
    PROGRESS("阅读进度"),
}

/** 书架排序的纯函数实现（可单测；不依赖 Android） */
object MangaSorter {

    /**
     * 排序。
     *
     * 所有模式都以 [NaturalSort] 比较标题作为**最后的稳定兜底**：
     * 否则「加入时间」相同（批量入库）或「都没有阅读记录」时顺序会随机跳动，
     * 用户每次进来看到的排列都不一样。
     */
    fun sort(books: List<MangaBook>, mode: MangaSort): List<MangaBook> = when (mode) {
        MangaSort.RECENT -> books.sortedWith(
            compareByDescending<MangaBook> { MangaTime.parse(it.lastRead) }
                .thenBy(NaturalSort.comparator) { it.title },
        )

        MangaSort.ADDED -> books.sortedWith(
            compareByDescending<MangaBook> { it.addedAt }
                .thenBy(NaturalSort.comparator) { it.title },
        )

        MangaSort.TITLE -> books.sortedWith(compareBy(NaturalSort.comparator) { it.title })

        MangaSort.PROGRESS -> books.sortedWith(
            // 没读过的排后面；读过的按比例从高到低（快读完的靠前，方便接着看）
            compareByDescending<MangaBook> { if (it.counted && it.pageCount > 0) 1 else 0 }
                .thenByDescending { if (it.pageCount > 0) it.pageIndex.toDouble() / it.pageCount else 0.0 }
                .thenBy(NaturalSort.comparator) { it.title },
        )
    }

    /** 标题/章节等文本匹配（大小写不敏感，空查询返回全部） */
    fun filter(books: List<MangaBook>, query: String): List<MangaBook> {
        val q = query.trim().lowercase()
        if (q.isEmpty()) return books
        return books.filter {
            it.title.lowercase().contains(q) || it.sourceUrl.lowercase().contains(q)
        }
    }
}
