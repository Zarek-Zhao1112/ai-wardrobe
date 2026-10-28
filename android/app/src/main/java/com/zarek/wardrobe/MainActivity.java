package com.zarek.wardrobe;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.view.View;
import android.webkit.ConsoleMessage;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebResourceRequest;
import android.widget.TextView;
import android.widget.Toast;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;

/**
 * 主界面就是一个 WebView，指向本机 8787。
 *
 * 前端一行都不用改：原来 `web/` 下那套原生 ES module + hash 路由照旧跑，
 * 因为 FastAPI 仍然把 `web/` 当静态目录服务。
 */
public class MainActivity extends Activity {

    private static final String TAG = "Wardrobe";
    private static final String BASE = "http://127.0.0.1:" + WardrobeService.PORT + "/";
    /** startActivityForResult 的请求码，随便取一个不会被别处用到的值 */
    private static final int REQ_FILE_CHOOSER = 0x5141;
    private static final int REQ_CAMERA = 0x5142;
    private PermissionRequest pendingCamera;

    private WebView web;
    private TextView hint;
    private final Handler ui = new Handler(Looper.getMainLooper());
    /**
     * onDestroy 之后置 true。轮询线程活满 6 分钟，期间随时可能往主线程 post
     * 一个访问 `web` / `hint` 的 runnable —— 那两个字段在 onDestroy 里已经废了，
     * 不判这一下就是 NPE（首次启动解压 10-20 秒，用户在等待页按返回就能必现）。
     */
    private volatile boolean dead = false;
    private boolean started = false;
    private Thread waitThread;
    /**
     * 正在等结果的 <input type=file> 回调。
     *
     * ★ 非 null 就意味着「选择器已经拉起来了，页面正等着 Uri」。
     * 这个回调**只能用一次**，用完必须置空；用户取消也必须回调 null 结账，
     * 否则它永远挂着，页面上那个文件按钮从此彻底死掉（第二次点连弹都不弹）。
     */
    private ValueCallback<Uri[]> pendingFileChooser;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        Intent svc = new Intent(this, WardrobeService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }

        // 旧WebView不会自动为debug包开放调试；正式包明确关闭。
        WebView.setWebContentsDebuggingEnabled((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);   // 页面是 http://127.0.0.1，不需要 file://，关掉少一个攻击面
        s.setAllowContentAccess(true); // 必须开：文件选择器交回来的是 content:// Uri
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return request.isForMainFrame() && openReference(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return openReference(Uri.parse(url));
            }
        });
        // ★ 必须用自己写的子类，不能直接 new WebChromeClient()。
        //   android.webkit.WebChromeClient 的 onShowFileChooser() 默认实现是
        //   `return false`（意思是"我不处理"）—— WebView 问了一圈没人接，
        //   点 <input type=file> 就完全无反应、不弹窗、不报错。
        //   2026-10-22 之前的版本就是这么写的，录入页/详情页的选择文件按钮全哑。
        web.setWebChromeClient(new ChooserClient());
        web.setBackgroundColor(Color.parseColor("#101725"));

        hint = new TextView(this);
        hint.setTextColor(Color.parseColor("#E8DCC8"));
        hint.setTextSize(16);
        hint.setBackgroundColor(Color.parseColor("#101725"));
        hint.setPadding(48, 240, 48, 48);
        hint.setText("正在启动衣橱服务…\n\n首次启动要解压 Python 运行时，大概 10–20 秒。");

        setContentView(hint);
        ensurePermissions();
        waitForServer();
    }

    /** §20：方法来源交给系统浏览器，衣橱页及未保存的表单留在原WebView。 */
    private boolean openReference(Uri uri) {
        if ("http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost())
                && uri.getPort() == WardrobeService.PORT) return false;
        if ("https".equals(uri.getScheme())) {
            try {
                Intent browser = new Intent(Intent.ACTION_VIEW, uri);
                browser.addCategory(Intent.CATEGORY_BROWSABLE);
                startActivity(browser);
            } catch (Exception e) {
                Toast.makeText(this, "暂时无法打开资料链接", Toast.LENGTH_SHORT).show();
            }
        }
        return true;
    }

    /** 相册直读要「所有文件访问」：普通读取权限在 Android 11+ 读不到 /sdcard/DCIM 的绝对路径。 */
    private void ensurePermissions() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                try {
                    startActivity(new Intent(
                            Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + getPackageName())));
                } catch (Exception e) {
                    Log.w(TAG, "打不开所有文件访问设置页", e);
                }
            }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    /** 轮询 8787 直到端口可连，再加载页面。不然 WebView 会拿到 404 并停在白屏。 */
    private void waitForServer() {
        waitThread = new Thread(() -> {
            for (int i = 0; i < 1200 && !dead; i++) {     // 最多等 6 分钟（首次解压慢）
                if (portOpen()) {
                    ui.post(() -> {
                        if (dead || web == null) return;  // Activity 已经销毁了，别再碰
                        setContentView(web);
                        web.loadUrl(BASE);
                    });
                    return;
                }
                if (i == 60) {
                    ui.post(() -> {
                        if (dead || hint == null) return;
                        hint.setText("还在启动…\n\n如果超过 1 分钟没动静，用 adb logcat 看 Wardrobe 标签。");
                    });
                }
                try {
                    Thread.sleep(300);
                } catch (InterruptedException e) {
                    return;
                }
            }
            ui.post(() -> {
                if (dead || hint == null) return;
                hint.setText("服务没起来。\n\n用 adb logcat -s Wardrobe 看具体原因。");
            });
        }, "wait-server");
        waitThread.start();
    }

    private boolean portOpen() {
        try (Socket so = new Socket()) {
            so.connect(new InetSocketAddress("127.0.0.1", WardrobeService.PORT), 400);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    // ───────────────────────── 文件选择器 ─────────────────────────
    //
    // 背景：前端 upload 走的是原生 <input type="file" accept="image/*">（web/js/view-add.js、
    // web/js/view-item.js）。WebView 自身没有 UI，页面点这个控件时它会问 WebChromeClient
    // "你要不要处理"。默认实现返回 false → 什么也不发生。
    //
    // 之前 Termux 桌面 PWA 壳（com.android.browser.webapps.pwa.PWAActivity）就是栽在这里，
    // 当时前端只能绕道走后端直读相册（/items/album/photo）。现在容器是我们自己的，
    // 直接把这个洞补上，两条路都通。

    private class ChooserClient extends WebChromeClient {

        /** §30：只批准本机衣橱页面的视频请求，系统权限由用户当场确认。 */
        @Override
        public void onPermissionRequest(PermissionRequest request) {
            runOnUiThread(() -> {
                if (!validCameraRequest(request)) { request.deny(); return; }
                cancelPendingCamera();
                pendingCamera = request;
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                        || checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                    finishCameraPermission(true);
                } else {
                    requestPermissions(new String[]{Manifest.permission.CAMERA}, REQ_CAMERA);
                }
            });
        }

        @Override
        public void onPermissionRequestCanceled(PermissionRequest request) {
            runOnUiThread(() -> { if (pendingCamera == request) pendingCamera = null; });
        }

        /**
         * 把页面 console 转发到 logcat。
         *
         * ★ 2026-10-22 补：这之前是个**诊断盲区**。WebView 默认把 console 消息吞掉，
         *   `adb logcat -s Wardrobe` 一条 JS 报错都看不到 —— 当天排查"页面写着
         *   后端没响应、但 /health 秒回"时，logcat 里干净得像是没问题，
         *   最后是靠 `adb forward` 接 WebView 调试端口才抓到真实状态。
         *   一个前端 SyntaxError 就能让整页停在"加载中…"，而壳这边毫无痕迹。
         *
         * 只记 WARN 及以上：INFO/DEBUG 里是页面自己的日志，量太大，会把有用信息冲掉。
         */
        @Override
        public boolean onConsoleMessage(ConsoleMessage msg) {
            if (msg == null) return false;
            int level = msg.messageLevel() == null ? 3 : msg.messageLevel().ordinal();
            // ordinal: TIP=0, LOG=1, WARNING=2, ERROR=3 —— 只要 WARNING/ERROR
            if (level >= 2) {
                String where = msg.sourceId() == null ? "?" : msg.sourceId();
                Log.w(TAG, "JS " + msg.messageLevel() + " " + where + ":" + msg.lineNumber()
                        + " " + msg.message());
            }
            return true;   // 已处理：别让框架再往系统日志刷一遍
        }

        @Override
        public boolean onShowFileChooser(WebView view,
                                        ValueCallback<Uri[]> callback,
                                        FileChooserParams params) {
            if (callback == null) return false;   // 没回调可结账，只能如实说没处理

            // 上一次如果没结账（比如上次选择器还开着就被切回来），先当取消结掉。
            // 不结的话新回调拿不到位置，旧的永远等不到结果。
            cancelPendingFileChooser();

            pendingFileChooser = callback;
            try {
                startActivityForResult(buildChooserIntent(params), REQ_FILE_CHOOSER);
                return true;
            } catch (Exception e) {
                // 拉起失败（极简 ROM 没装任何文件选择器 / ActivityNotFoundException）。
                // ★ 自己结账 + return true，不能再 return false：
                //   return false 的语义是"我没处理，框架你自己看着办"，Chromium 会按取消
                //   再结一次同一个 callback，等于同一个 ValueCallback 被结两次。
                Log.e(TAG, "拉起文件选择器失败", e);
                cancelPendingFileChooser();
                return true;
            }
        }
    }

    /**
     * 按页面的 accept / multiple 拼 intent。
     *
     * 优先用框架给的 {@link WebChromeClient.FileChooserParams#createIntent()}：它自己
     * 会处理「单类型只 setType、多类型用 * / * + EXTRA_MIME_TYPES」这件容易写错的事。
     * 手写只在它抛异常时兜底。
     */
    private Intent buildChooserIntent(WebChromeClient.FileChooserParams params) {
        Intent intent = null;
        if (params != null) {
            try {
                intent = params.createIntent();
            } catch (Exception e) {
                Log.w(TAG, "createIntent 失败，改手写", e);
            }
        }
        if (intent == null) {
            intent = new Intent(Intent.ACTION_GET_CONTENT);
            intent.addCategory(Intent.CATEGORY_OPENABLE);   // 必须：只用能 openInputStream 的 provider
            intent.setType("*/*");
        }

        // accept 里可能混进 ".jpg" 这种扩展名（Chromium 原样透传）。
        // setType(".jpg") 不是合法 MIME → 一个 Activity 都匹配不上 → 又是"点了没反应"。
        List<String> mimes = new ArrayList<>();
        String[] accept = params == null ? null : params.getAcceptTypes();
        if (accept != null) {
            for (String a : accept) {
                String t = a == null ? "" : a.trim();
                if (t.contains("/") && !"*/*".equals(t)) mimes.add(t);
            }
        }
        if (!mimes.isEmpty()) {
            if (mimes.size() == 1) {
                intent.setType(mimes.get(0));                  // 单类型：只 setType
                intent.removeExtra(Intent.EXTRA_MIME_TYPES);   // 别双设，部分 ROM 会二次过滤
            } else {
                intent.setType("*/*");
                intent.putExtra(Intent.EXTRA_MIME_TYPES, mimes.toArray(new String[0]));
            }
        }

        // 录入页使用 multiple 多选；详情页补图仍按单选处理。
        if (params != null
                && params.getMode() == WebChromeClient.FileChooserParams.MODE_OPEN_MULTIPLE) {
            intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        } else {
            intent.removeExtra(Intent.EXTRA_ALLOW_MULTIPLE);
        }
        return intent;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_FILE_CHOOSER && pendingFileChooser != null) {
            Uri[] picked = null;
            if (resultCode == RESULT_OK && data != null) {
                picked = readPickedUris(data);
            }
            // ★ 成功、取消、data 为空、Uri 读不了，四种情况都必须回调一次。
            //   "用户取消"不回调是 WebView 最经典的坑：回调泄漏，按钮从此点不动。
            pendingFileChooser.onReceiveValue(picked);
            pendingFileChooser = null;
        }
        // 无条件调 super：将来若加 Fragment 之类，漏调会让他们永远收不到结果。
        super.onActivityResult(requestCode, resultCode, data);
    }

    /**
     * 单选看 getData()，多选看 getClipData()；两种都可能为空。
     *
     * ★ 每个 Uri 都先试着 open 一下再交出去。某些 OEM picker（含小米部分版本）
     * 返回的 content:// 不带 FLAG_GRANT_READ_URI_PERMISSION，页面那边会拿到一个
     * "看起来选中了、一读就抛 SecurityException / NotReadableError"的 File ——
     * 用户分不清是没选到还是上传失败。这里提前判掉，读不了就当没选。
     */
    private Uri[] readPickedUris(Intent data) {
        ClipData clip = data.getClipData();
        if (clip != null && clip.getItemCount() > 0) {
            List<Uri> ok = new ArrayList<>();
            for (int i = 0; i < clip.getItemCount(); i++) {
                Uri u = clip.getItemAt(i).getUri();
                if (u != null && readable(u)) ok.add(u);
            }
            return ok.isEmpty() ? null : ok.toArray(new Uri[0]);
        }
        Uri single = data.getData();
        return (single != null && readable(single)) ? new Uri[]{single} : null;
    }

    /** 探一下这个 Uri 能不能真的读出来（内容只有几 KB 时开销可忽略）。 */
    private boolean readable(Uri u) {
        try (java.io.InputStream in = getContentResolver().openInputStream(u)) {
            return in != null;
        } catch (Exception e) {
            Log.e(TAG, "这个 Uri 读不了，provider 可能没给 grant: " + u, e);
            return false;
        }
    }

    /** 把挂着的回调按"取消"结掉。可重复调用。 */
    private void cancelPendingFileChooser() {
        ValueCallback<Uri[]> cb = pendingFileChooser;
        pendingFileChooser = null;
        if (cb != null) {
            try {
                cb.onReceiveValue(null);
            } catch (Exception e) {
                Log.w(TAG, "回调已失效，忽略", e);
            }
        }
    }

    private boolean validCameraRequest(PermissionRequest request) {
        if (dead || !started || web == null || request == null) return false;
        Uri origin = request.getOrigin();
        Uri current = Uri.parse(web.getUrl() == null ? "" : web.getUrl());
        String[] resources = request.getResources();
        return "http".equals(origin.getScheme()) && "127.0.0.1".equals(origin.getHost())
                && origin.getPort() == WardrobeService.PORT
                && "http".equals(current.getScheme()) && "127.0.0.1".equals(current.getHost())
                && current.getPort() == WardrobeService.PORT
                && resources.length == 1 && PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(resources[0]);
    }

    private void cancelPendingCamera() {
        PermissionRequest request = pendingCamera; pendingCamera = null;
        if (request != null) request.deny();
    }

    private void finishCameraPermission(boolean granted) {
        PermissionRequest request = pendingCamera; pendingCamera = null;
        if (request == null) return;
        if (granted && validCameraRequest(request)) request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
        else request.deny();
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        if (code == REQ_CAMERA) finishCameraPermission(results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED);
    }

    @Override
    protected void onStart() {
        super.onStart();
        started = true;
    }

    @Override
    protected void onStop() {
        started = false;
        cancelPendingCamera();
        if (web != null) web.evaluateJavascript("window.dispatchEvent(new Event('wardrobe-camera-stop'))", null);
        super.onStop();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        // 先停掉轮询线程，再废字段，最后才销毁 WebView —— 顺序反了会留下野指针
        dead = true;
        if (waitThread != null) waitThread.interrupt();
        // WebView 销毁前把可能挂着的文件回调结掉，否则页面侧会留一个永不 resolve 的 Promise
        cancelPendingFileChooser();
        cancelPendingCamera();
        if (web != null) {
            // 先从父容器摘下来再 destroy：某些 WebView 版本在仍 attached 时 destroy
            // 会打 "WebView.destroy() called while still attached"，甚至直接崩。
            android.view.ViewParent parent = web.getParent();
            if (parent instanceof android.view.ViewGroup) {
                ((android.view.ViewGroup) parent).removeView(web);
            }
            web.destroy();
            web = null;
        }
        hint = null;
        super.onDestroy();
    }
}
