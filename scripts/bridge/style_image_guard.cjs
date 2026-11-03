// §38：一次固定文生图；不接受原图、模型覆盖或额外工具参数。
const fs=require('fs'),path=require('path');
exports.selfCheck=()=>0;
exports.guard=directory=>{
 try{
  const expected=JSON.parse(fs.readFileSync(path.join(directory,'guard.json'),'utf8'));
  const event=JSON.parse(fs.readFileSync(0,'utf8')),input=event.tool_input;
  if(event.tool_name!=='ImageGen'||!input||typeof input!=='object'||Array.isArray(input))return 2;
  if(Object.keys(input).some(k=>!['prompt','output_dir','size','revise'].includes(k)))return 2;
  if(input.prompt!==expected.prompt||input.size!=='1024x1024'||input.revise!==false||typeof input.output_dir!=='string'||path.resolve(input.output_dir)!==path.resolve(expected.output_dir))return 2;
  fs.writeFileSync(path.join(directory,'tool-claimed'),'1',{flag:'wx'});return 0;
 }catch{return 2;}
};
