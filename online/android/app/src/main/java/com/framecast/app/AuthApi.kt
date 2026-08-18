package com.framecast.app

import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/**
 * Klien API akun FrameCast (Cloudflare Worker):
 * register, login, daftar device milik akun.
 */
class AuthApi(private val baseUrl: String) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()
    private val json = "application/json; charset=utf-8".toMediaType()

    private fun post(path: String, body: JSONObject, token: String? = null): JSONObject {
        val req = Request.Builder()
            .url(baseUrl + path)
            .post(body.toString().toRequestBody(json))
            .apply { if (token != null) header("Authorization", "Bearer $token") }
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: "{}"
            return JSONObject(text)
        }
    }

    fun register(email: String, password: String, device: JSONObject): JSONObject {
        val body = JSONObject().put("email", email).put("password", password).put("device", device)
        return post("/api/register", body)
    }

    fun login(email: String, password: String, device: JSONObject): JSONObject {
        val body = JSONObject().put("email", email).put("password", password).put("device", device)
        return post("/api/login", body)
    }

    /** Daftar device milik akun (online? model? type?). */
    fun devices(token: String): JSONArray {
        val req = Request.Builder()
            .url(baseUrl + "/api/devices?token=" + java.net.URLEncoder.encode(token, "UTF-8"))
            .get()
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string() ?: "[]"
            return JSONObject(text).optJSONArray("devices") ?: JSONArray()
        }
    }
}
