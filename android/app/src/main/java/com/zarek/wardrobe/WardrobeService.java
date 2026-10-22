package com.zarek.wardrobe;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * 常驻前台服务：跑 Python 侧的 uvicorn，并让进程拿到前台优先级。
 *
 * 为什么需要它：Termux 那套是靠系统对前台 App 的宽松策略，一旦切走就可能被冻住
 * （记忆里就有一条「服务卡死，点 Termux 图标几秒恢复」的历史）。这里用前台服务 +
 * 常驻通知，系统就不会随便回收。
 */
public class WardrobeService extends Service {

    private static final String TAG = "Wardrobe";
    private static final String CHANNEL_ID = "wardrobe_service";
    private static final int NOTIFY_ID = 0x8787;
    public static final int PORT = 8787;

    private volatile boolean started = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannel();
        startForeground(NOTIFY_ID, buildNotification("正在启动衣橱服务…"));
        new Thread(this::bootPython, "py-boot").start();
    }

    private void bootPython() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(getApplicationContext()));
            }
            Python py = Python.getInstance();
            // ★ 数据目录必须是 app 私有目录：APK 里的 Python 源码是只读的，
            //   而项目默认会把 db 写到源码根下的 data/ 里。
            //   config.py 支持 WARDROBE_DATA_DIR 覆盖，不用改任何业务代码。
            String dataDir = getFilesDir().getAbsolutePath();
            Log.i(TAG, "启动 Python 服务，dataDir=" + dataDir);
            py.getModule("server").callAttr("start", dataDir, PORT);
            started = true;
            updateNotification("衣橱服务运行中 · 127.0.0.1:" + PORT);
        } catch (Throwable t) {
            // 这里一定要打全栈：Python 侧的异常在 Kotlin/Java 里只看到一句话，
            // 不打栈的话真机上根本不知道为什么起不来。
            Log.e(TAG, "Python 服务启动失败", t);
            updateNotification("启动失败：" + t.getMessage());
        }
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                    CHANNEL_ID, "衣橱服务", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private Notification buildNotification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        // ★ 只用 NEW_TASK，不要 CLEAR_TOP：MainActivity 已经改成 singleTask，
        //   带上 CLEAR_TOP 的语义是"销毁栈里那个实例再建新的" —— 点一下常驻通知
        //   就会把页面重载一遍（录入页填的内容、刚选好的图全没了）。
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) flags |= PendingIntent.FLAG_IMMUTABLE;
        PendingIntent pi = PendingIntent.getActivity(this, 0, open, flags);

        Notification.Builder b = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return b.setContentTitle("AI 穿搭助手")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_menu_manage)
                .setOngoing(true)
                .setContentIntent(pi)
                .build();
    }

    private void updateNotification(String text) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) nm.notify(NOTIFY_ID, buildNotification(text));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // 被系统杀掉后重启：onCreate 已经处理过启动，这里只需返回 STICKY
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
