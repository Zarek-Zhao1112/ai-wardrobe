// §31：只允许本次固定查询的一次网页搜索；拒绝附加工具、任意参数与重复调用。
const fs=require('fs'),path=require('path');
function guard(directory,input){
 const expected=JSON.parse(fs.readFileSync(path.join(directory,'query.json'),'utf8'));
 const event=typeof input==='string'?JSON.parse(input):input,payload=event.tool_input;
 if(event.tool_name!=='WebSearch'||!payload||Array.isArray(payload)||Object.keys(payload).some(k=>k!=='query')||payload.query!==expected.query)return 2;
 try{fs.writeFileSync(path.join(directory,'search-claimed'),'1',{flag:'wx'});}catch{return 2;}
 return 0;
}
module.exports={guard};
if(require.main===module){let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',part=>{input+=part;if(input.length>30000)process.exit(2)});process.stdin.on('end',()=>{try{process.exit(guard(process.argv[2],input))}catch{process.exit(2)}});}
