# Android APK

APK包含原生WebView壳、Python3.14、FastAPI与web静态资源。业务源码来自仓库app/和web/，独立构建目录里的副本由脚本生成。

## 本机工具链

需要JDK17、Android SDK、Gradle、Python3.14与项目配置的离线wheel。工具链放在仓库外，不复制进Git。

将toolchain.env.example复制到仓库根.local/toolchain.env，填写工具目录。环境变量优先于示例默认值：

| 配置 | 用途 |
|---|---|
| AIW_BUILD_DIR | 独立且为ASCII短路径的构建目录，默认C:/wbuild/aiw |
| AIW_TOOLCHAIN | 含jdk/与gradle/的本机工具目录 |
| AIW_SDK_DIR | SDK路径 |
| AIW_BUILD_PYTHON | 构建期Python3.14解释器 |
| AIW_MAVEN_DIR | 本机离线Maven缓存，可按实际环境配置 |

SDK可以使用指向本机工具目录的ASCII链接。Gradle和Chaquopy在本机中文路径下有兼容性问题，源码可以在中文路径，构建输出使用短路径。

## 构建

```bash
sh android/build_apk.sh
```

流程：先验证最终目标不包含源码且没有外部链接，再复制Android骨架；sync_sources.py按清单覆盖app/web、删除过期副本并排除宿主pyc；核对资源清单、源码与骨架；执行assembleDebug。该同步不会清理应用的用户数据目录，也不删除构建server.py或本机.env。

默认产物C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk。

```powershell
adb install -r "C:/wbuild/aiw/app/build/outputs/apk/debug/app-debug.apk"
```

使用电脑本地APK路径；同签名-r覆盖安装保留应用数据。安装后应核对实际base.apk指纹、前端资源及数据快照，不以构建成功代替装机核验。

## 目录与版本

- app/src/main/java：WebView壳、服务与文件选择器。
- app/src/main/python/server.py：先指定应用私有数据目录，再启动服务。
- wheels/：必要离线依赖，保留在Git中。
- tools/sync_sources.py：构建目录路径预检和源码精确同步。
- tools/apk_diff.py：APK条目与增量打包空洞诊断。
- 其余tools/：本机诊断辅助脚本，常规入口仍为build_apk.sh。

构建脚本沿用本机.env；个人安装包可能包含配置，所以APK不提交或随源码发布。发布给其他人前应使用无私密配置的环境。源码没有.env时，脚本会清理构建区旧.env，避免继承上次配置。

历史迁移和Termux记录见docs/history/与deploy/legacy-termux/，当前维护说明见docs/DEVELOPMENT.md。
