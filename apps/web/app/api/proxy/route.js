import {NextResponse} from 'next/server';
const uuid='[0-9a-fA-F-]{36}';
export async function POST(request){
 const headers={'Cache-Control':'no-store'};
 try{
  const {path,method='GET',body}=await request.json();
  const allowed=(method==='GET'&&(path?.split('?')[0]==='/v1/memories'||path?.split('?')[0]==='/v1/search'||path?.split('?')[0]==='/v1/suggestions'||new RegExp(`^/v1/suggestions/${uuid}/sources$`).test(path)||new RegExp(`^/v1/(memories|jobs)/${uuid}(/(history|sources|erasure|audit))?$`).test(path)))||(method==='PATCH'&&new RegExp(`^/v1/memories/${uuid}$`).test(path))||(method==='POST'&&(new RegExp(`^/v1/memories/${uuid}/(forget|erasure/retry)$`).test(path)||new RegExp(`^/v1/suggestions/${uuid}/(approve|reject)$`).test(path)));
  if(!allowed)return NextResponse.json({error:{code:'invalid_route'}},{status:400,headers});
  const base=new URL(process.env.API_URL||'http://127.0.0.1:8000');
  if(base.protocol!=='https:'&&!(base.protocol==='http:'&&['127.0.0.1','localhost','[::1]'].includes(base.hostname)))throw Error('unsafe upstream');
  const authorization=request.headers.get('authorization');
  if(!authorization||authorization.length>4100)return NextResponse.json({error:{code:'invalid_credentials'}},{status:401,headers});
  const response=await fetch(new URL(path,base),{method,headers:{Authorization:authorization,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,cache:'no-store',redirect:'error',signal:AbortSignal.timeout(5000)});
  return new NextResponse(await response.text(),{status:response.status,headers:{...headers,'Content-Type':'application/json','X-Request-ID':response.headers.get('X-Request-ID')||''}});
 }catch{return NextResponse.json({error:{code:'service_unavailable'}},{status:503,headers});}
}
