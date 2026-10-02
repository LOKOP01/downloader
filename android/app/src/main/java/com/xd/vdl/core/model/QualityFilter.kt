package com.xd.vdl.core.model

/**
 * 清晰度下拉要摆哪些档位（与桌面版 `app/core/models.py` 同一套规则）。
 *
 * 抖音一条作品常有近 20 档（同分辨率多个码率），整列铺开要滚好几屏；B 站也有
 * 4K30 与 1080P60 并存的情况。规则两条：
 * 1. **有 60fps 级（≥[HIGH_FPS]）的源时，30fps 那些直接不显示**；
 * 2. 剩下的里分辨率只保留前三高的种类（同分辨率的多条码流算一档），太低的省略。
 *
 * 帧率或分辨率读不出来的档位（`最高画质`／`视频`）不参与判断、始终保留 ——
 * 拿不准的不动，也避免把唯一的 60fps 在低分辨率档被规则 2 砍掉后列表变空。
 */
private val RES_PATTERN = Regex("""(\d{2,5})×(\d{2,5})""")

// 只有高度时（`720p`、B 站 `1080P60`）：P 后面允许跟 1~2 位帧率，
// 但不能跟另一个分辨率数字
private val HEIGHT_PATTERN =
    Regex("""(\d{3,4})\s*p(?![0-9]{3})""", RegexOption.IGNORE_CASE)

private val FPS_PATTERNS = listOf(
    Regex("""(\d{2,3})\s*fps""", RegexOption.IGNORE_CASE),  // 60fps
    Regex("""(\d{2,3})\s*帧"""),                              // 60帧
    Regex("""p(\d{2,3})(?![0-9])""", RegexOption.IGNORE_CASE), // B 站 1080P60
)

/** 「60fps 级」门槛：≥50 算高帧率（30fps 那条就是 30） */
const val HIGH_FPS = 50

/** 标签里的分辨率「面积」，用来比较档位高低；取不到返回 0 */
fun resolutionArea(label: String): Int {
    RES_PATTERN.find(label)?.let {
        return it.groupValues[1].toInt() * it.groupValues[2].toInt()
    }
    HEIGHT_PATTERN.find(label)?.let {
        val h = it.groupValues[1].toInt()
        // 只有高度时按 16:9 折算宽度，只为让同一来源的档位能互相比较
        return h * h * 16 / 9
    }
    return 0
}

/** 标签里的帧率；拿不到返回 0。不会把 `2500kbps`、`H.265` 里的数字当帧率 */
fun fpsInLabel(label: String): Int {
    for (pattern in FPS_PATTERNS) {
        pattern.find(label)?.let { return it.groupValues[1].toInt() }
    }
    return 0
}

/** 按上面的规则筛出该显示（也就能选）的档位，顺序保持原样 */
fun dropdownQualities(all: List<Quality>, keep: Int = 3): List<Quality> {
    if (all.isEmpty()) return all
    val fps = all.map { fpsInLabel(it.label) }
    val pool = if (fps.any { it >= HIGH_FPS }) {
        all.indices.filter { fps[it] == 0 || fps[it] >= HIGH_FPS }
    } else {
        all.indices.toList()
    }
    val areas = pool.map { resolutionArea(all[it].label) }
    val known = areas.filter { it > 0 }.distinct().sortedDescending()
    if (known.size <= keep) return pool.map { all[it] }
    val allowed = known.take(keep).toSet()
    return pool.filterIndexed { i, _ -> areas[i] <= 0 || areas[i] in allowed }
        .map { all[it] }
}
