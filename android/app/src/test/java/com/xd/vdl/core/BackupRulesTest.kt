package com.xd.vdl.core

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * 备份排除规则回归（B-04）。
 *
 * ⚠️ 这里验证的是**规则文件本身**：Manifest 引用了它们、且凭据类 prefs
 * 确实在排除列表里、普通配置未被误伤。
 *
 * 至于「系统备份服务是否真的执行了这些规则」，属于 Android 框架行为，
 * JVM 单测无法验证 —— 本测试不声称覆盖那部分。
 */
class BackupRulesTest {

    private val resXml = File("src/main/res/xml")

    private fun read(name: String): String {
        val f = File(resXml, name)
        assertTrue("缺少资源文件 ${f.path}", f.exists())
        return f.readText()
    }

    /** 三份凭据 prefs + WebView 数据目录，两份规则都必须排除 */
    private val sensitive = listOf(
        "vdl_cookies.xml",
        "vdl_jm_session.xml",
        "vdl_net.xml",
    )

    @Test fun cloudAndTransferRulesBothExcludeCredentials() {
        val rules = read("data_extraction_rules.xml")
        val cloud = rules.substringAfter("<cloud-backup>").substringBefore("</cloud-backup>")
        val transfer = rules.substringAfter("<device-transfer>").substringBefore("</device-transfer>")
        for (name in sensitive) {
            // 只写 cloud 不写 device-transfer 是常见漏项：换机迁移仍会把凭据带过去
            assertTrue("cloud-backup 未排除 $name", cloud.contains(name))
            assertTrue("device-transfer 未排除 $name", transfer.contains(name))
        }
        assertTrue("云备份未排除 WebView 数据目录", cloud.contains("app_webview"))
        assertTrue("设备迁移未排除 WebView 数据目录", transfer.contains("app_webview"))
    }

    @Test fun legacyRulesExcludeTheSameCredentials() {
        val rules = read("backup_rules.xml")
        for (name in sensitive) {
            assertTrue("Android 11 及以下规则未排除 $name", rules.contains(name))
        }
        assertTrue(rules.contains("app_webview"))
    }

    @Test fun nonSensitivePreferencesAreStillBackedUp() {
        val all = read("backup_rules.xml") + read("data_extraction_rules.xml")
        // 普通配置不该被排除，否则用户换机会丢掉下载目录设置
        for (keep in listOf("vdl_save.xml", "vdl_jm.xml")) {
            assertFalse("普通配置 $keep 不应被排除", all.contains(keep))
        }
    }

    @Test fun manifestWiresBothRuleFiles() {
        val manifest = File("src/main/AndroidManifest.xml").readText()
        assertTrue(manifest.contains("android:allowBackup=\"true\""))
        assertTrue(manifest.contains("android:fullBackupContent=\"@xml/backup_rules\""))
        assertTrue(manifest.contains("android:dataExtractionRules=\"@xml/data_extraction_rules\""))
    }
}
