#ifndef SHR_ADD2_HPP
#define SHR_ADD2_HPP

#include <cstdint>
#include <memory>
#include <utility>
#include <vector>

#include <emp-tool/runtime/io/io_channel.h>

namespace scucse::crypto
{
enum class ShrAdd2Pid
{
    P0,
    P1,
};

/// @brief Concept for schemes supporting additive secret sharing functionalities.
///        \f$ \mathcal{F}_{\mathrm{ShrAddMod}},
///        \mathcal{F}_{\mathrm{ShrAddHash}},
///        \mathcal{F}_{\mathrm{ShrAddEqualityTest}} \f$
template <ShrAdd2Pid PARTY, uint64_t ELL, template <ShrAdd2Pid, uint64_t> typename SCHEME>
concept ShrAdd2Cnstr =
    std::constructible_from<SCHEME<PARTY, ELL>, emp::IOChannel*> &&
    requires(
        SCHEME<PARTY, ELL> scheme,
        uint32_t s1,
        uint32_t s2,
        uint32_t mv,
        const std::vector<uint8_t>& sv,
        std::vector<uint8_t>& nsv,       // non-const for recv_data
        const uint8_t key16[16]
    )
    {
        { scheme.mod(s1, mv) } -> std::same_as<uint32_t>;
        { scheme.hash(sv.data(), sv.size(), key16) } -> std::same_as<uint32_t>;
        { scheme.equalityTest(s1, s2) } -> std::same_as<uint32_t>;
        { scheme.share(s1) } -> std::same_as<uint32_t>;
        { scheme.recvShare() } -> std::same_as<uint32_t>;
        { scheme.shareBytes(sv) } -> std::same_as<std::vector<uint8_t>>;
        { scheme.recvBytes() } -> std::same_as<std::vector<uint8_t>>;
        { scheme.shareKey(key16) } -> std::same_as<std::vector<uint8_t>>;
        { scheme.recvKey() } -> std::same_as<std::vector<uint8_t>>;
        { scheme.send(s1) } -> std::same_as<void>;
        { scheme.recv() } -> std::same_as<uint32_t>;
        { scheme.send_data(sv.data(), sv.size()) } -> std::same_as<void>;
        { scheme.recv_data(nsv.data(), nsv.size()) } -> std::same_as<void>;
    };
} // namespace scucse::crypto

#endif // !SHR_ADD2_HPP
