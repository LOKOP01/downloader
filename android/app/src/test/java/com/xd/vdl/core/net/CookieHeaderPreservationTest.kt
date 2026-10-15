package com.xd.vdl.core.net
import okhttp3.*
import org.junit.Assert.*
import org.junit.Test
import java.net.ServerSocket
import kotlin.concurrent.thread
class CookieHeaderPreservationTest {
 private fun exchange(fixed: Boolean): List<String> {
  val headers = mutableListOf<String>()
  ServerSocket(0).use { server ->
   server.soTimeout = 5000
   val worker = thread(isDaemon=true) {
    repeat(2) { index -> server.accept().use { socket ->
     val reader = socket.getInputStream().bufferedReader()
     var cookie = ""
     while (true) {
      val line = reader.readLine() ?: break
      if (line.isEmpty()) break
      if (line.startsWith("Cookie:",true)) cookie=line.substringAfter(':').trim()
     }
     headers.add(cookie)
     val set=if(index==0) "Set-Cookie: guest_token=visitor; Path=/\r\n" else ""
     socket.getOutputStream().write(("HTTP/1.1 200 OK\r\n"+set+"Content-Length: 2\r\nConnection: close\r\n\r\n{}").toByteArray())
    } }
   }
   val jar=object:CookieJar {
    var cookies=emptyList<Cookie>()
    override fun saveFromResponse(url:HttpUrl,cookies:List<Cookie>) { this.cookies=cookies }
    override fun loadForRequest(url:HttpUrl)=cookies.filter { it.matches(url) }
   }
   val builder=OkHttpClient.Builder().cookieJar(jar)
   if(fixed) builder.addNetworkInterceptor(PreserveLoginCookieInterceptor())
   val client=builder.build()
   repeat(2) {
    client.newCall(Request.Builder().url("http://127.0.0.1:"+server.localPort+"/").header("Cookie","SESSDATA=test-login; DedeUserID=test-user").build()).execute().use { it.body?.string() }
   }
   worker.join(5000)
   assertFalse(worker.isAlive)
   client.connectionPool.evictAll()
  }
  return headers
 }
 @Test fun fixedRequestsKeepLoginAndServerSessionCookies() {
  val sent=exchange(true)
  assertTrue(sent[0].contains("SESSDATA=test-login"))
  assertTrue(sent[1].contains("SESSDATA=test-login"))
  assertTrue(sent[1].contains("DedeUserID=test-user"))
  assertTrue(sent[1].contains("guest_token=visitor"))
 }
 @Test fun explicitLoginWinsAndValuesWithEqualsArePreserved() {
  assertEquals("SESSDATA=user==; ttwid=guest", PreserveLoginCookieInterceptor.merge("SESSDATA=stale; ttwid=guest", "SESSDATA=user=="))
 }
 @Test fun demonstrateOkHttpOverwritesExplicitLoginCookieAfterSetCookie() {
  val sent=exchange(false)
  assertTrue(sent[0].contains("SESSDATA=test-login"))
  assertEquals("guest_token=visitor",sent[1])
 }
}
