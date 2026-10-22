package com.zarek.wardrobe;

import com.chaquo.python.android.PyApplication;

/**
 * 用 Chaquopy 的 PyApplication：它在 onCreate 里就把 CPython 解释器起起来，
 * 省得每个入口都判断一次 {@code Python.isStarted()}。
 */
public class WardrobeApp extends PyApplication {
}
