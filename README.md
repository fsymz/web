# 院内导航微信小程序

这是面向患者和访客的微信小程序原生工程。用户手动选择当前位置和目的地，程序在代码包内计算并模拟院内路线，不读取 GPS、蓝牙、Wi-Fi 或其他定位信息。

## 离线导航与在线语音

- 核心导航不需要业务后端、数据库、对象存储或 CDN。公开目的地、路线与 13 张楼层图都在 `miniprogram/` 代码包内。
- 项目不建立患者账号，不持久化身份、手动选择的位置、目的地或路线历史。用户主动录制的当次音频会交由 WechatSI 在线识别，待播报文本会交由在线 TTS 合成；本程序不自行持久化或缓存录音、识别文本、待播报文本和临时合成文件。
- 语音播报使用 `app.json` 中锁定的 WechatSI `0.3.5`。自动欢迎语、导航步骤、重播和导诊回复都通过在线 TTS 播放。
- 语音识别只会在用户明确点击语音按钮后启动，麦克风权限也只在该操作后申请。打开页面、查看导诊面板或听欢迎语都不会自动录音。
- 无网络或插件不可用时，语音播报和识别不可用，但手动选择、路线计算、地图、步骤文字和按钮操作仍然可用，界面会显示简短的文字状态。用户拒绝麦克风权限时，仅影响语音识别；网络和插件可用时，在线 TTS 播报仍可工作。

## 目的地与路线规则

- 患者目的地保留“消控室”和“计算机机房”。
- 同层路线使用本层自动校验通过的候选路径；共享同一锚点的目的地返回同区域提示。只有完成人工净空、锚点、路线和现场复核后，候选路径才能获得患者发布授权。
- 步进导航按完整原始折线计算距离和转向；不足播报精度的微小段会合并成复合提示，保留内部真实转向且不播报“约0米”。
- 跨层路线比较起点到各可用电梯的真实可通行距离，选择更近的电梯；距离相同时按 `shaftId` 升序稳定选择。
- 起点段和目标段始终使用同一电梯井；无共同可用电梯时会提示咨询导医台或现场工作人员。

## 导入微信开发者工具

1. 选择“导入项目”，目录选择本 `小程序工程` 目录。
2. 共享的 `project.config.json` 固定使用 `touristappid`，`miniprogramRoot` 为 `miniprogram/`。
3. 真实 AppID 只配置在开发者工具生成的、已忽略的 `project.private.config.json` 中，不要改动或提交共享配置里的游客 AppID。
4. `miniprogram/app.json` 已经声明 WechatSI，不需要合并额外片段，也不在 `app.json.permission` 中声明 `scope.record`。
5. 使用真实 AppID 时，还需要在微信小程后台开通插件权限，并在真机上验证欢迎语、步骤播报、重播、输入回复、主动语音识别和断网降级。

## Web 离线演示

Web 演示与小程序共享 `miniprogram/data` 和 `miniprogram/utils` 的确定性 bundle，用于查看路线文字、楼层图和分段动画。浏览器演示不接入 WechatSI，因此不提供语音播报或识别。

```powershell
node scripts\build-web-bundle.js --output web-demo\navigation.bundle.js
python -m http.server 41739 --bind 127.0.0.1 --directory .
```

然后访问 `http://127.0.0.1:41739/web-demo/`。

## 安装与重建

```powershell
npm install --ignore-scripts
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

地图优化脚本只处理楼层图：

```powershell
python scripts\optimize-assets.py `
  --source-floor-dir '..\放入院内导航页面目录下\放入images文件夹\floor-maps' `
  --output-assets-dir 'miniprogram\assets'
node scripts\build-web-bundle.js --output web-demo\navigation.bundle.js
```

## 验收

自动候选门禁用于本地开发、开发者工具预览和真机验收，不代表患者发布授权：

```powershell
npm run verify:candidate
```

严格患者发布门禁默认失败关闭。13 层净空、95 个锚点、730 条基础路线、1,462 个跨层行程、现场事实以及 Android/iPhone 语音验收都必须具有当前且绑定哈希的批准证据。对应的结构化登记校验器尚未实现，因此当前严格门禁不会通过；完成校验器和全部人工证据前，不得把候选包标记为患者发布版：

```powershell
npm run verify
# 等同于 npm run verify:release
```

完整开发检查：

```powershell
npm test
python -m pytest tests\python -q
npm run test:web
node scripts\generate-runtime-config.js --check
python scripts\generate-floor-nav-paths.py --check
python scripts\generate-same-floor-paths.py --check
node scripts\check-routes.js
python scripts\audit-route-connectivity.py
node scripts\check-syntax.js
python scripts\verify-release.py --candidate
git diff --check
```

严格患者发布检查需单独运行，并且当前预期失败：

```powershell
npm run verify:release
```

微信开发者工具自动冒烟只能证明编译、路由与页面状态正常，不能代替 Android 和 iPhone 的真机听觉、麦克风权限与断网验证，也不能替代尚未实现的结构化登记校验。
