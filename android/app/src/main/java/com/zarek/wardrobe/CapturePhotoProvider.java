package com.zarek.wardrobe;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/** §47：只把本次相机输出的缓存 JPEG 授权给系统相机。 */
public final class CapturePhotoProvider extends ContentProvider {
    private static final String[] COLUMNS = {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};

    private static File directory(Context context) {
        return new File(context.getCacheDir(), "wardrobe-capture");
    }

    static String authority(Context context) {
        return context.getPackageName() + ".capture";
    }

    static File createPhoto(Context context) throws IOException {
        File dir = directory(context);
        if (!dir.isDirectory() && !dir.mkdirs()) throw new IOException("无法建立相机缓存");
        long cutoff = System.currentTimeMillis() - 24L * 60 * 60 * 1000;
        File[] files = dir.listFiles();
        if (files != null) {
            for (File file : files) {
                if (validName(file.getName()) && file.lastModified() < cutoff) file.delete();
            }
        }
        return File.createTempFile("capture-", ".jpg", dir);
    }

    static Uri uriFor(Context context, File file) {
        return new Uri.Builder().scheme("content").authority(authority(context))
                .appendPath(file.getName()).build();
    }

    private static boolean validName(String name) {
        return name != null && name.matches("capture-[a-zA-Z0-9-]+\\.jpg");
    }

    private File photo(Uri uri) {
        if (uri.getPathSegments().size() != 1 || !validName(uri.getLastPathSegment())) {
            throw new IllegalArgumentException("无效的拍照地址");
        }
        return new File(directory(getContext()), uri.getLastPathSegment());
    }

    @Override public boolean onCreate() { return true; }
    @Override public String getType(Uri uri) { photo(uri); return "image/jpeg"; }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        File file = photo(uri);
        if (!file.isFile()) throw new FileNotFoundException("照片不存在");
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.parseMode(mode));
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File file = photo(uri);
        String[] columns = projection == null ? COLUMNS : projection;
        MatrixCursor cursor = new MatrixCursor(columns, 1);
        if (!file.isFile()) return cursor;
        Object[] row = new Object[columns.length];
        for (int i = 0; i < columns.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(columns[i])) row[i] = file.getName();
            if (OpenableColumns.SIZE.equals(columns[i])) row[i] = file.length();
        }
        cursor.addRow(row);
        return cursor;
    }

    @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException(); }
    @Override public int update(Uri uri, ContentValues values, String selection, String[] args) { throw new UnsupportedOperationException(); }
    @Override public int delete(Uri uri, String selection, String[] args) { throw new UnsupportedOperationException(); }
}
