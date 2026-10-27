// Opt-in finite rolling profile. Reuse the accepted parser, isolation guard and B encoder.
// The included independent worker is compiled unchanged under an unused entry point.
#define main independent_worker_entry
#include "worker_child.cpp"
#undef main
const std::string cc="d0d6181bf911e3c5552ee2c6f4bb91db6362b767ccbd7686c07fdf79a8d1f943";
void cs(const std::string&type,const std::string&fields="",const std::string&payload=""){
 need(payload.size()<=65536);auto s="{\"protocol\":\"hv2-cont03a/1\",\"type\":"+quote(type)+",\"attempt\":"+quote(attempt)+",\"seq\":"+std::to_string(outseq++)+",\"payload_bytes\":"+std::to_string(payload.size())+fields+"}";
 need(s.size()<=2048);output_bytes+=4+s.size()+payload.size();need(output_bytes<=96*1024*1024);uint32_t n=htonl(s.size());std::cout.write((char*)&n,4);std::cout<<s;std::cout.write(payload.data(),payload.size());std::cout.flush();need(bool(std::cout));
}
std::pair<M,std::string> cr(const std::string&kind,const std::set<std::string>&extra){
 uint32_t n;exact(&n,4);n=ntohl(n);need(n&&n<=2048);std::string s(n,'\0');exact(s.data(),n);auto m=parse(s);std::set<std::string> keys={"protocol","type","attempt","seq","payload_bytes"};keys.insert(extra.begin(),extra.end());std::set<std::string> actual;for(auto&[k,v]:m)actual.insert(k);
 need(actual==keys&&m.at("protocol")=="hv2-cont03a/1"&&m.at("type")==kind&&num(m,"seq")==inseq++);if(!bound){attempt=m.at("attempt");bound=true;}need(m.at("attempt")==attempt);auto bytes=num(m,"payload_bytes");need(bytes<=65536);if(kind!="TRUST_CHUNK"&&kind!="NAL_CHUNK")need(bytes==0);total+=4+n+bytes;need(total<=320ULL*1024*1024);std::string payload(bytes,'\0');exact(payload.data(),bytes);return {m,payload};
}
void cu(const videotrust::HistoricalUpdate&u,const std::string&phase,const M&b,videotrust::HistoricalExtractor&e,int error=0){
 need(update_id<=32768);auto raw=worker_updates::encode(u,phase,attempt,b.at("trust_digest"),cc,e.stop_reason(),e.consumed_feeds(),error);auto digest=hash(raw),id=std::to_string(update_id++);
 cs("UPDATE_BEGIN",",\"update\":"+id+",\"size\":"+std::to_string(raw.size())+",\"digest\":"+quote(digest)+",\"items\":"+std::to_string(u.items.size())+",\"missing\":"+std::to_string(u.missing.size()));
 for(size_t at=0;at<raw.size();at+=65536)cs("UPDATE_CHUNK",",\"update\":"+id+",\"offset\":"+std::to_string(at),raw.substr(at,65536));
 cs("UPDATE_END",",\"update\":"+id+",\"digest\":"+quote(digest));
}
int main(int argc,char**argv){try{
 need(argc>=3);pid_t parent=std::stol(argv[1]);need(getppid()==parent);need(prctl(PR_SET_PDEATHSIG,SIGKILL)==0);need(getppid()==parent);
 rlimit mem{512ULL<<20,512ULL<<20},cpu{60,60},core{0,0};need(!setrlimit(RLIMIT_AS,&mem)&&!setrlimit(RLIMIT_CPU,&cpu)&&!setrlimit(RLIMIT_CORE,&core));
 need(syscall(__NR_close_range,3,~0U,0)==0);need(OPENSSL_init_crypto(OPENSSL_INIT_NO_LOAD_CONFIG,nullptr)==1);need(OSSL_PROVIDER_load(nullptr,"default")!=nullptr);
 auto md=EVP_MD_fetch(nullptr,"SHA256",nullptr);need(md);EVP_MD_free(md);auto sig=EVP_SIGNATURE_fetch(nullptr,"RSA",nullptr);need(sig);EVP_SIGNATURE_free(sig);auto km=EVP_KEYMGMT_fetch(nullptr,"RSA",nullptr);need(km);EVP_KEYMGMT_free(km);unsigned char entropy[32];need(RAND_bytes(entropy,32)==1);OPENSSL_cleanse(entropy,32);hash("");need(syscall(__NR_close_range,3,~0U,0)==0);
 int abi=guard(parent);
 if(std::string(argv[2])=="check"){errno=0;need(open("/etc/passwd",O_RDONLY)<0&&errno==EPERM);errno=0;need(socket(AF_INET,SOCK_STREAM,0)<0&&errno==EPERM);errno=0;need(fork()<0&&errno==EPERM);}
 else need(std::string(argv[2])=="normal");
 cs("READY",",\"abi\":"+std::to_string(abi)+",\"guard\":\"landlock-seccomp-pdeath\",\"capability\":\"B-h264-cont03a\",\"configuration\":"+quote(cc));if(std::string(argv[2])=="check")return 0;
 auto b=cr("BEGIN",{"request","run","source","generation","source_digest","source_bytes","nal_count","trust_digest","trust_bytes","camera","binding","profile","cut","policy","configuration","files"}).first;
 need(b.at("configuration")==cc&&b.at("profile")=="native-hv2-cont09-v1"&&b.at("policy")=="standalone-candidate-v1"&&num(b,"source_bytes")==256*1024*1024&&num(b,"nal_count")==32768&&num(b,"trust_bytes")>0&&num(b,"trust_bytes")<=32768);
 auto count=std::stoul(b.at("files"));need(count>0&&count<=182);std::string trust;
 while(trust.size()<num(b,"trust_bytes")){auto[h,p]=cr("TRUST_CHUNK",{"offset"});need(num(h,"offset")==trust.size()&&!p.empty()&&trust.size()+p.size()<=num(b,"trust_bytes"));trust+=p;}cr("TRUST_END",{});need(hash(trust)==b.at("trust_digest"));
 auto made=videotrust::HistoricalExtractor::Create(videotrust::Codec::H264,b.at("run"),{1048576,1024,2048,256,32768,262144});need(made.ok());auto e=std::move(made.value());need(e.SetTrustedCertificate(trust).code==videotrust::ErrorCode::Ok);cs("ADMITTED",",\"trust_digest\":"+quote(b.at("trust_digest")));
 std::set<std::string> sources;uint64_t ordinal=0,bytes=0;
 for(unsigned file=0;file<count;++file){
  auto f=cr("SOURCE_BEGIN",{"ordinal","source","generation","original_sha256","source_digest","source_bytes","nal_count"}).first;
  need(num(f,"ordinal")==file&&sources.insert(f.at("source")).second&&num(f,"source_bytes")>0&&num(f,"source_bytes")<=8*1024*1024&&num(f,"nal_count")>0&&num(f,"nal_count")<=1024&&ordinal+num(f,"nal_count")<=32768&&bytes+num(f,"source_bytes")<=256*1024*1024);
  SHA256_CTX ctx;SHA256_Init(&ctx);uint64_t filebytes=0;
  for(uint64_t local=0;local<num(f,"nal_count");++local,++ordinal){
   auto h=cr("NAL_BEGIN",{"ordinal","token","size","digest"}).first;need(num(h,"ordinal")==ordinal&&num(h,"size")>=5&&num(h,"size")<=1048576);std::string nal;
   while(nal.size()<num(h,"size")){auto[ch,p]=cr("NAL_CHUNK",{"ordinal","offset"});need(num(ch,"ordinal")==ordinal&&num(ch,"offset")==nal.size()&&!p.empty()&&nal.size()+p.size()<=num(h,"size"));nal+=p;}
   auto end=cr("NAL_END",{"ordinal"}).first;need(num(end,"ordinal")==ordinal&&hash(nal)==h.at("digest")&&nal.substr(0,4)==std::string("\0\0\0\1",4));filebytes+=nal.size();need(filebytes<=num(f,"source_bytes"));SHA256_Update(&ctx,nal.data(),nal.size());
   auto u=e.Feed(std::span<const uint8_t>((const uint8_t*)nal.data(),nal.size()),unhex(h.at("token")));
   if(!u.ok()){auto tail=e.Finish();cu(tail,"error",b,e,int(u.error().code));return 32;}cu(u.value(),"feed",b,e);
  }
  unsigned char digest[32];SHA256_Final(digest,&ctx);std::ostringstream out;for(auto x:digest)out<<std::hex<<std::setw(2)<<std::setfill('0')<<int(x);
  auto end=cr("SOURCE_END",{"ordinal","source_digest"}).first;need(num(end,"ordinal")==file&&filebytes==num(f,"source_bytes")&&out.str()==f.at("source_digest")&&end.at("source_digest")==out.str());bytes+=filebytes;
  cs("SOURCE_END",",\"ordinal\":"+std::to_string(file)+",\"source_digest\":"+quote(out.str()));
 }
 cr("FINISH_INPUT",{});auto tail=e.Finish();cu(tail,"finish",b,e);cs("TERMINAL",",\"consumed\":"+std::to_string(ordinal)+",\"completeness\":\"INCOMPLETE\"");return 0;
 }catch(...){return 20;}}
