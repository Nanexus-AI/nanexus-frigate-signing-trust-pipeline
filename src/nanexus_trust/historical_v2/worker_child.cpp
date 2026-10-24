// Isolated private D1.5 worker: native B only after guard and trust admission.
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <linux/landlock.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <openssl/sha.h>
#include <openssl/crypto.h>
#include <openssl/provider.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include "videotrust/historical_observation.hpp"
#include <optional>
#include "worker_updates.hpp"
#include <arpa/inet.h>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <iomanip>
#include <string>
#include <vector>
#include <stdexcept>
using M=std::map<std::string,std::string>;
void need(bool x){if(!x)throw std::runtime_error("protocol");}
std::string hash(const std::string&s){unsigned char b[32];SHA256((const unsigned char*)s.data(),s.size(),b);std::ostringstream o;for(auto c:b)o<<std::hex<<std::setw(2)<<std::setfill('0')<<int(c);return o.str();}
std::string quote(const std::string&s){std::string o="\"";for(char c:s){need(c>=32&&c<=126);if(c=='"'||c=='\\')o+='\\';o+=c;}return o+'"';}
M parse(const std::string&s){size_t p=0;auto ws=[&](){while(p<s.size()&&(s[p]==' '||s[p]=='\n'||s[p]=='\r'||s[p]=='\t'))++p;};auto text=[&](){need(p<s.size()&&s[p++]=='"');std::string v;while(p<s.size()&&s[p]!='"'){char c=s[p++];if(c=='\\'){need(p<s.size());c=s[p++];need(c=='"'||c=='\\'||c=='/');}need(c>=32&&c<=126);v+=c;need(v.size()<=512);}need(p<s.size()&&s[p++]=='"');return v;};M m;ws();need(p<s.size()&&s[p++]=='{');ws();while(p<s.size()&&s[p]!='}'){auto k=text();ws();need(p<s.size()&&s[p++]==':');ws();std::string v;bool quoted=p<s.size()&&s[p]=='"';if(p<s.size()&&s[p]=='"')v=text();else{size_t at=p;while(p<s.size()&&s[p]>='0'&&s[p]<='9')++p;need(p>at);v=s.substr(at,p-at);need(v.size()<=19&&(v.size()==1||v[0]!='0'));}std::set<std::string> numbers={"seq","payload_bytes","offset","ordinal","size","source_bytes","nal_count","trust_bytes"};need(numbers.count(k)?!quoted:quoted);need(m.emplace(k,v).second);ws();if(p<s.size()&&s[p]==','){++p;ws();need(p<s.size()&&s[p]!='}');}else break;}need(p<s.size()&&s[p++]=='}');ws();need(p==s.size());return m;}
uint64_t num(const M&m,const std::string&k){const auto&s=m.at(k);need(!s.empty()&&s.size()<=19&&s.find_first_not_of("0123456789")==std::string::npos);auto n=std::stoull(s);need(n<INT64_MAX);return n;}
void exact(void*b,size_t n){auto p=(char*)b;while(n){ssize_t k=read(0,p,n);need(k>0);p+=k;n-=k;}}
std::string attempt="startup";bool bound=false;uint64_t outseq=0,inseq=0,total=0,output_bytes=0,update_id=0;
const std::string configuration="c2178317b889d0854348aae185eb96f01854bfa8e201385108b320d0ffa5c961";std::string fault;
void send(const std::string&type,const std::string&fields="",const std::string&payload=""){
 need(payload.size()<=65536);std::string s="{\"protocol\":\"hv2-d1.5/1\",\"type\":"+quote(type)+",\"attempt\":"+quote(attempt)+",\"seq\":"+std::to_string(outseq++)+",\"payload_bytes\":"+std::to_string(payload.size())+fields+"}";
 need(s.size()<=2048);output_bytes+=4+s.size()+payload.size();need(output_bytes<=2097152);uint32_t n=htonl(s.size());std::cout.write((char*)&n,4);std::cout<<s;std::cout.write(payload.data(),payload.size());std::cout.flush();need(bool(std::cout));
}
void update(const videotrust::HistoricalUpdate& u,const std::string& phase,const M&begin,videotrust::HistoricalStop stop,uint64_t consumed,int error=0){
 need(update_id<=1024);auto raw=worker_updates::encode(u,phase,attempt,begin.at("trust_digest"),configuration,stop,consumed,error);auto digest=hash(raw);auto id=std::to_string(update_id++);
 send("UPDATE_BEGIN",",\"update\":"+id+",\"size\":"+std::to_string(raw.size())+",\"digest\":"+quote(digest)+",\"items\":"+std::to_string(u.items.size())+",\"missing\":"+std::to_string(u.missing.size()));
 for(size_t at=0;at<raw.size();at+=65536)send("UPDATE_CHUNK",",\"update\":"+id+",\"offset\":"+std::to_string(at),raw.substr(at,65536));
 send("UPDATE_END",",\"update\":"+id+",\"digest\":"+quote(digest));
}
std::string unhex(const std::string& s){need(!s.empty()&&s.size()<=512&&s.size()%2==0);std::string o;for(size_t i=0;i<s.size();i+=2){auto a=std::string("0123456789abcdef").find(s[i]),b=std::string("0123456789abcdef").find(s[i+1]);need(a<16&&b<16);o+=char(a*16+b);}return o;}
std::pair<M,std::string> receive(const std::string&kind,const std::set<std::string>&extra){uint32_t n;exact(&n,4);n=ntohl(n);need(n&&n<=2048);std::string s(n,'\0');exact(s.data(),n);auto m=parse(s);std::set<std::string> keys={"protocol","type","attempt","seq","payload_bytes"};keys.insert(extra.begin(),extra.end());std::set<std::string> actual;for(auto&[k,v]:m)actual.insert(k);need(actual==keys&&m.at("protocol")=="hv2-d1.5/1"&&m.at("type")==kind&&num(m,"seq")==inseq++);if(!bound){attempt=m.at("attempt");bound=true;}need(m.at("attempt")==attempt);auto bytes=num(m,"payload_bytes");need(bytes<=65536);if(kind!="TRUST_CHUNK"&&kind!="NAL_CHUNK")need(bytes==0);total+=4+n+bytes;need(total<=10*1024*1024);std::string payload(bytes,'\0');exact(payload.data(),bytes);return {m,payload};}
int guard(pid_t expected){need(getppid()==expected);need(prctl(PR_SET_PDEATHSIG,SIGKILL)==0);need(getppid()==expected);rlimit mem{512ULL<<20,512ULL<<20},cpu{10,10},file{2ULL<<20,2ULL<<20},core{0,0};need(!setrlimit(RLIMIT_AS,&mem)&&!setrlimit(RLIMIT_CPU,&cpu)&&!setrlimit(RLIMIT_FSIZE,&file)&&!setrlimit(RLIMIT_CORE,&core));need(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)==0);int abi=syscall(__NR_landlock_create_ruleset,nullptr,0,LANDLOCK_CREATE_RULESET_VERSION);need(abi>=1);landlock_ruleset_attr a{};a.handled_access_fs=(1ULL<<13)-1;if(abi>=2)a.handled_access_fs|=1ULL<<13;if(abi>=3)a.handled_access_fs|=1ULL<<14;int fd=syscall(__NR_landlock_create_ruleset,&a,sizeof(a),0);need(fd>=0);need(syscall(__NR_landlock_restrict_self,fd,0)==0);close(fd);
 std::vector<sock_filter> f;f.push_back(BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(seccomp_data,arch)));f.push_back(BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,AUDIT_ARCH_X86_64,1,0));f.push_back(BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS));f.push_back(BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(seccomp_data,nr)));f.push_back(BPF_JUMP(BPF_JMP|BPF_JGE|BPF_K,0x40000000,0,1));f.push_back(BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_KILL_PROCESS));
 for(int n:{__NR_read,__NR_write,__NR_close,__NR_fstat,__NR_lseek,__NR_brk,__NR_mmap,__NR_mprotect,__NR_munmap,__NR_mremap,__NR_madvise,__NR_rt_sigaction,__NR_rt_sigprocmask,__NR_rt_sigreturn,__NR_sigaltstack,__NR_getpid,__NR_getppid,__NR_gettid,__NR_futex,__NR_sched_yield,__NR_clock_gettime,__NR_nanosleep,__NR_clock_nanosleep,__NR_pause,__NR_exit,__NR_exit_group}){f.push_back(BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,n,0,1));f.push_back(BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW));}f.push_back(BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM));sock_fprog program{(unsigned short)f.size(),f.data()};need(prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&program)==0);return abi;}
int main(int argc,char**argv){try{need(argc>=3);pid_t parent=std::stol(argv[1]);fault=argv[2];need(getppid()==parent);need(prctl(PR_SET_PDEATHSIG,SIGKILL)==0);need(getppid()==parent);
 rlimit earlymem{512ULL<<20,512ULL<<20},earlycpu{10,10},earlycore{0,0},earlyfile{2ULL<<20,2ULL<<20};need(!setrlimit(RLIMIT_AS,&earlymem)&&!setrlimit(RLIMIT_CPU,&earlycpu)&&!setrlimit(RLIMIT_CORE,&earlycore)&&!setrlimit(RLIMIT_FSIZE,&earlyfile));
 need(syscall(__NR_close_range,3,~0U,0)==0);
 need(OPENSSL_init_crypto(OPENSSL_INIT_NO_LOAD_CONFIG,nullptr)==1);need(OSSL_PROVIDER_load(nullptr,"default")!=nullptr);
 auto md=EVP_MD_fetch(nullptr,"SHA256",nullptr);need(md);EVP_MD_free(md);
 auto sig=EVP_SIGNATURE_fetch(nullptr,"RSA",nullptr);need(sig);EVP_SIGNATURE_free(sig);
 auto km=EVP_KEYMGMT_fetch(nullptr,"RSA",nullptr);need(km);EVP_KEYMGMT_free(km);
 unsigned char entropy[32];need(RAND_bytes(entropy,sizeof(entropy))==1);OPENSSL_cleanse(entropy,sizeof(entropy));hash("");need(syscall(__NR_close_range,3,~0U,0)==0);int abi=guard(parent);if(fault=="check"){need(argc==4);errno=0;int fd=open(argv[3],O_RDONLY);bool fs=fd<0&&errno==EPERM;errno=0;int sk=socket(AF_INET,SOCK_STREAM,0);bool net=sk<0&&errno==EPERM;errno=0;int pid=fork();bool proc=pid<0&&errno==EPERM;need(fs&&net&&proc);}
 send("READY",",\"abi\":"+std::to_string(abi)+",\"guard\":\"landlock-seccomp-pdeath\",\"capability\":\"B-h264-v1\",\"configuration\":"+quote(configuration));
 if(fault=="check")return 0;
 if(fault=="transport-check"){
 videotrust::HistoricalUpdate u;u.run_id="synthetic-transport";u.generating_feed_ordinal=7;u.report_generation=2;u.fresh_report=true;u.retired_prefix_ordinal=1;
 videotrust::HistoricalItem i;i.received_ordinal=1;i.source_token="same-payload-occurrence";i.nalu_type='P';i.raw_status='N';i.revision=3;i.report_generation=2;i.finalized=true;i.state=videotrust::HistoricalSigningState::IntegrityNegative;i.axes={2,3,0,true,-9,20};u.items.push_back(i);u.missing.push_back({1,3,2});M b{{"trust_digest",std::string(64,'a')}};update(u,"feed",b,videotrust::HistoricalStop::None,8);return 0;}

 if(fault=="crash"){*(volatile int*)0=1;return 13;}
 if(fault=="hang"){signal(SIGTERM,SIG_IGN);while(true)pause();}
 if(fault=="memory"){void*p=malloc(768ULL<<20);need(!p);return 14;}
 if(fault=="output"){while(true)std::cout<<std::string(16384,'x')<<std::flush;}
 if(fault=="stderr"){while(true)write(2,"xxxxxxxxxxxxxxxx",16);}
 if(fault=="truncated"){std::cout.write("\0\0",2);std::cout.flush();return 15;}
 auto begin=receive("BEGIN",{"request","run","source","generation","source_digest","source_bytes","nal_count","trust_digest","trust_bytes","camera","binding","profile","cut","policy","configuration"}).first;need(num(begin,"source_bytes")>0&&num(begin,"source_bytes")<=8*1024*1024&&num(begin,"nal_count")>0&&num(begin,"nal_count")<=1024&&num(begin,"trust_bytes")>0&&num(begin,"trust_bytes")<=65536&&((begin.at("profile")=="synthetic-hv2-d1-v1"&&begin.at("policy")=="synthetic-single-source-v1")||(begin.at("profile")=="native-hv2-d1.5-v1"&&begin.at("policy")=="native-single-source-v1")));need(begin.at("configuration")==configuration);if(begin.at("profile")=="native-hv2-d1.5-v1")need(num(begin,"trust_bytes")<=32768);std::string trust;
 while(trust.size()<num(begin,"trust_bytes")){auto[h,p]=receive("TRUST_CHUNK",{"offset"});need(num(h,"offset")==trust.size()&&!p.empty()&&trust.size()+p.size()<=num(begin,"trust_bytes"));trust+=p;}receive("TRUST_END",{});need(hash(trust)==begin.at("trust_digest"));std::optional<videotrust::HistoricalExtractor> extractor;
 if(begin.at("profile")=="native-hv2-d1.5-v1"){
 need(trust.size()<=32768);auto made=videotrust::HistoricalExtractor::Create(videotrust::Codec::H264,begin.at("run"),{1048576,1024,2048,256,1024,8192});
 if(!made.ok()){send("NATIVE_ERROR",",\"code\":"+std::to_string(int(made.error().code))+",\"stop\":3,\"consumed\":0,\"stage\":\"create\",\"configuration\":"+quote(configuration)+",\"trust_digest\":"+quote(begin.at("trust_digest")));return 30;}extractor.emplace(std::move(made.value()));
 auto err=extractor->SetTrustedCertificate(trust);if(err.code!=videotrust::ErrorCode::Ok){send("NATIVE_ERROR",",\"code\":"+std::to_string(int(err.code))+",\"stop\":"+std::to_string(int(extractor->stop_reason()))+",\"consumed\":0,\"stage\":\"trust\",\"configuration\":"+quote(configuration)+",\"trust_digest\":"+quote(begin.at("trust_digest")));return 31;}
 }
 send("ADMITTED",",\"trust_digest\":"+quote(begin.at("trust_digest")));
 uint64_t bytes=0;SHA256_CTX ctx;SHA256_Init(&ctx);std::set<std::string> tokens;
 for(uint64_t i=0;i<num(begin,"nal_count");++i){auto h=receive("NAL_BEGIN",{"ordinal","token","size","digest"}).first;need(num(h,"ordinal")==i&&num(h,"size")>0&&num(h,"size")<=1048576&&tokens.insert(unhex(h.at("token"))).second);std::string nal;while(nal.size()<num(h,"size")){auto[ch,p]=receive("NAL_CHUNK",{"ordinal","offset"});need(num(ch,"ordinal")==i&&num(ch,"offset")==nal.size()&&!p.empty()&&nal.size()+p.size()<=num(h,"size"));nal+=p;}auto end=receive("NAL_END",{"ordinal"}).first;need(num(end,"ordinal")==i&&hash(nal)==h.at("digest")&&nal.size()>=5&&nal.substr(0,4)==std::string("\0\0\0\1",4));if(extractor){
 if(fault=="feed-hang"){signal(SIGTERM,SIG_IGN);while(true)pause();}if(fault=="feed-crash"){*(volatile int*)0=1;}
 auto u=extractor->Feed(std::span<const uint8_t>((const uint8_t*)nal.data(),nal.size()),unhex(h.at("token")));
 if(!u.ok()){auto tail=extractor->Finish();update(tail,"error",begin,extractor->stop_reason(),extractor->consumed_feeds(),int(u.error().code));return 32;}
 update(u.value(),"feed",begin,extractor->stop_reason(),extractor->consumed_feeds());}
 bytes+=nal.size();need(bytes<=8*1024*1024);SHA256_Update(&ctx,nal.data(),nal.size());if(!extractor)send("ACK",",\"ordinal\":"+std::to_string(i)+",\"token\":"+quote(h.at("token"))+",\"consumed\":"+std::to_string(i+1));}
 receive("FINISH_INPUT",{});unsigned char digest[32];SHA256_Final(digest,&ctx);std::ostringstream o;for(auto c:digest)o<<std::hex<<std::setw(2)<<std::setfill('0')<<int(c);need(bytes==num(begin,"source_bytes")&&o.str()==begin.at("source_digest"));if(extractor){if(fault=="finish-hang"){signal(SIGTERM,SIG_IGN);while(true)pause();}if(fault=="finish-crash"){*(volatile int*)0=1;}auto tail=extractor->Finish();update(tail,"finish",begin,extractor->stop_reason(),extractor->consumed_feeds());}
 send("TERMINAL",",\"consumed\":"+begin.at("nal_count")+",\"completeness\":\"INCOMPLETE\"");return 0;
 }catch(...){return 20;}}
