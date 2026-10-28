// §23：轻量工具前置校验，不加载应用/第三方模块；异常统一拒绝。
const fs = require('fs'), path = require('path');
exports.selfCheck = () => 0;
exports.guard = directory => {
  try {
    const expected = JSON.parse(fs.readFileSync(path.join(directory, 'guard.json'), 'utf8'));
    const event = JSON.parse(fs.readFileSync(0, 'utf8'));
    const input = event.tool_input;
    if (event.tool_name !== 'ImageGen' || !input || typeof input !== 'object') return 2;
    if (path.resolve(input.image1 || '') !== path.resolve(expected.image1) || path.resolve(input.output_dir || '') !== path.resolve(expected.output_dir)) return 2;
    if (input.image2 || input.image3 || input.prompt !== expected.prompt || input.size !== '1024x1024') return 2;
    if (expected.revise !== undefined && input.revise !== expected.revise) return 2;
    fs.writeFileSync(path.join(directory, 'tool-claimed'), '1', { flag: 'wx' });
    return 0;
  } catch {
    process.stderr.write('本次仅允许一次固定来源和输出目录的图像编辑。\n');
    return 2;
  }
};
