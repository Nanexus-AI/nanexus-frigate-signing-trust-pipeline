#pragma once
// Private bounded transport for the existing Toolkit B API; no verifier logic.
#include "videotrust/historical_observation.hpp"
#include <string>
#include <stdexcept>
namespace worker_updates {
constexpr size_t cap=1048576;
inline void append(std::string& out,const std::string& s){if(s.size()>cap-out.size())throw std::length_error("update-cap");out+=s;}
inline std::string quoted(const std::string& s){std::string o="\"";for(unsigned char c:s){if(c=='"'||c=='\\'){o+='\\';o+=c;}else if(c<32){const char* h="0123456789abcdef";o+="\\u00";o+=h[c>>4];o+=h[c&15];}else o+=c;}return o+'"';}
inline std::string hex(const std::string& s){std::string out;const char*h="0123456789abcdef";for(unsigned char c:s){out+=h[c>>4];out+=h[c&15];}return out;}
template<class T> std::string optional(const std::optional<T>& v){return v?std::to_string(*v):"null";}
inline std::string encode(const videotrust::HistoricalUpdate& u,const std::string& phase,
 const std::string& attempt,const std::string& trust,const std::string& config,
 videotrust::HistoricalStop stop,uint64_t consumed,int error=0){
 if(u.items.size()>2048||u.missing.size()>2048)throw std::length_error("update-items");
 std::string o;append(o,"{\"run_id\":"+quoted(u.run_id)+",\"attempt\":"+quoted(attempt)+",\"phase\":"+quoted(phase)+",\"trust\":"+quoted(trust)+",\"configuration\":"+quoted(config)+",\"stop\":"+std::to_string(int(stop))+",\"error\":"+std::to_string(error)+",\"consumed\":"+std::to_string(consumed)+",\"generating_feed_ordinal\":"+std::to_string(u.generating_feed_ordinal)+",\"report_generation\":"+std::to_string(u.report_generation)+",\"fresh_report\":"+(u.fresh_report?"true":"false")+",\"retired_prefix_ordinal\":"+optional(u.retired_prefix_ordinal)+",\"items\":[");
 bool comma=false;for(const auto&i:u.items){if(i.source_token.size()>256)throw std::length_error("token");if(comma)append(o,",");comma=true;
 append(o,"{\"received_ordinal\":"+std::to_string(i.received_ordinal)+",\"source_token_hex\":"+quoted(hex(i.source_token))+",\"nalu_type\":"+quoted(std::string(1,i.nalu_type))+",\"raw_status\":"+quoted(std::string(1,i.raw_status))+",\"revision\":"+std::to_string(i.revision)+",\"report_generation\":"+std::to_string(i.report_generation)+",\"finalized\":"+(i.finalized?"true":"false")+",\"state\":"+std::to_string(int(i.state))+",\"axes\":{\"authenticity\":"+std::to_string(i.axes.authenticity)+",\"provenance\":"+std::to_string(i.axes.provenance)+",\"combined\":"+std::to_string(i.axes.combined)+",\"key_changed\":"+(i.axes.key_changed?"true":"false")+",\"raw_signing_start\":"+optional(i.axes.raw_signing_start)+",\"raw_signing_end\":"+optional(i.axes.raw_signing_end)+"}}");}
 append(o,"],\"missing\":[");comma=false;for(const auto&m:u.missing){if(comma)append(o,",");comma=true;append(o,"{\"left_received\":"+optional(m.left_received)+",\"right_received\":"+optional(m.right_received)+",\"report_generation\":"+std::to_string(m.report_generation)+"}");}append(o,"]}");return o;
}
}
