package com.xd.vdl.core.download

import com.xd.vdl.core.Platform
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.net.ServerSocket
import java.nio.file.Files
import kotlin.concurrent.thread

class DownloaderIntegrityTest {
    @Test fun truncatedResponseDoesNotCreateCompletedFile() = runBlocking {
        val bytes = ByteArray(4096) { 42 }
        ServerSocket(0).use { server ->
            server.soTimeout = 5000
            val sender = thread(isDaemon = true) {
                server.accept().use { socket ->
                    val reader = socket.getInputStream().bufferedReader()
                    while (!reader.readLine().isNullOrEmpty()) { /* request headers */ }
                    val header = "HTTP/1.1 200 OK\r\nContent-Length: ${bytes.size + 100}\r\n" +
                        "Content-Type: video/mp4\r\nConnection: close\r\n\r\n"
                    socket.getOutputStream().write(header.toByteArray(Charsets.US_ASCII))
                    socket.getOutputStream().write(bytes)
                    socket.getOutputStream().flush()
                }
            }
            val folder = Files.createTempDirectory("downloader-test-").toFile()
            try {
                val file = File(folder, "video.mp4")
                val result = runCatching {
                    Downloader.download(
                        "http://127.0.0.1:${server.localPort}/short",
                        Platform.UNKNOWN, file,
                    ) { _, _ -> }
                }
                assertTrue("short response should fail", result.isFailure)
                assertFalse("partial content must not become a completed file", file.exists())
            } finally {
                sender.join(5000)
                folder.deleteRecursively()
            }
        }
    }
}
