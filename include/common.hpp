#ifndef COMMON_HPP
#define COMMON_HPP

#include <algorithm>
#include <bit>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <emp-tool/emp-tool.h>
#include <sodium.h>

#ifndef __linux__
#error "Unsupported: this project requires a Linux operating system"
#endif

static_assert(
    std::endian::native == std::endian::little,
    "Unsupported: this project requires a little-endian architecture"
);

namespace scucse::crypto::math
{

    template <uint64_t ELL>
    inline constexpr uint8_t RING_MASK8 =
        (ELL == 8) ? static_cast<uint8_t>(0xFF) : static_cast<uint8_t>((1U << ELL) - 1);

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 8)uint8_t ringAdd8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) { return static_cast<uint8_t>(static_cast<unsigned>(a) + static_cast<unsigned>(b)); }
        else                    { return static_cast<uint8_t>((static_cast<unsigned>(a) + static_cast<unsigned>(b)) & RING_MASK8<ELL>); }
    }

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 8)uint8_t ringSub8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) { return static_cast<uint8_t>(static_cast<unsigned>(a) - static_cast<unsigned>(b)); }
        else                    { return static_cast<uint8_t>((static_cast<unsigned>(a) - static_cast<unsigned>(b)) & RING_MASK8<ELL>); }
    }

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 8)uint8_t ringMul8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) {
            return static_cast<uint8_t>(static_cast<unsigned>(a) * static_cast<unsigned>(b));
        } else {
            return static_cast<uint8_t>(
                (static_cast<unsigned>(a) * static_cast<unsigned>(b)) & RING_MASK8<ELL>);
        }
    }

    //   32-bit (ELL ≤ 32)  

    template <uint64_t ELL>
    inline constexpr uint32_t RING_MASK32 =
        (ELL >= 32) ? static_cast<uint32_t>(0xFFFFFFFF)
                    : static_cast<uint32_t>((UINT64_C(1) << ELL) - 1);

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 32)uint32_t ringAdd32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a + b; }
        return (a + b) & RING_MASK32<ELL>;
    }

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 32)uint32_t ringSub32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a - b; }
        return (a - b) & RING_MASK32<ELL>;
    }

    template <uint64_t ELL> requires(ELL >= 1 && ELL <= 32)uint32_t ringMul32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a * b; }
        return (a * b) & RING_MASK32<ELL>;
    }

} // namespace scucse::crypto::math

namespace scucse::crypto
{

    /// Valid ring element bit-widths for scalar operations.
    inline constexpr uint64_t RING_SCALAR_ELL_MIN = 1;
    inline constexpr uint64_t RING_SCALAR_ELL_MAX = 31;

    /// Cryptographically secure random ring element in Z_{2^ell}, 1 ≤ ell ≤ 31.
    inline uint32_t ringRand(uint64_t ell)
    {
        if (ell < RING_SCALAR_ELL_MIN || ell > RING_SCALAR_ELL_MAX)
            throw std::invalid_argument(
                "ringRand: ell must be in [" + std::to_string(RING_SCALAR_ELL_MIN)
                + ", " + std::to_string(RING_SCALAR_ELL_MAX) + "]");

        static std::once_flag sodium_init_once;
        std::call_once(sodium_init_once, []() {
            if (sodium_init() < 0)
                throw std::runtime_error("libsodium init failed");
        });

        uint32_t v;
        randombytes_buf(&v, sizeof(v));
        return v & ((UINT64_C(1) << ell) - 1);
    }

    /// AES-DM hash: H(preimage, key) → Z_{2^ell}.
    /// Key is truncated or zero-padded to exactly 16 bytes.
    /// Preimage is arbitrary length — processed in 16-byte blocks
    /// via Davies-Meyer chain: H_i = AES(key, H_{i-1} ^ block_i) ^ H_{i-1}.
    inline uint32_t hashAESDM
    (
        const uint8_t* preimage,
        size_t preimage_len,
        const uint8_t* key,
        size_t key_len,
        uint64_t ell
    )
    {
        if (ell < RING_SCALAR_ELL_MIN || ell > RING_SCALAR_ELL_MAX)
            throw std::invalid_argument(
                "hashAESDM: ell must be in [" + std::to_string(RING_SCALAR_ELL_MIN)
                + ", " + std::to_string(RING_SCALAR_ELL_MAX) + "]");

        if (key_len > 16)
            throw std::invalid_argument("hashAESDM: key must be ≤ 16 bytes");
        if (key == nullptr && key_len > 0)
            throw std::invalid_argument("hashAESDM: key must not be null");
        if (preimage == nullptr && preimage_len > 0)
            throw std::invalid_argument("hashAESDM: preimage must not be null");

        using namespace emp;
        uint8_t k[16] = {};
        if (key_len > 0) std::memcpy(k, key, std::min(key_len, size_t(16)));

        block key_block;
        std::memcpy(&key_block, k, sizeof(key_block));
        PRP prp(key_block);

        // Davies-Meyer chain: H_0 = 0, H_i = AES(key, H_{i-1} ^ M_i) ^ H_{i-1}
        block h = _mm_setzero_si128();
        size_t pos = 0;
        while (pos + 16 <= preimage_len)
        {
            block m;
            std::memcpy(&m, preimage + pos, sizeof(m));
            block x = h ^ m;
            prp.permute_block(&x, 1);
            h = x ^ h;
            pos += 16;
        }
        // Final (possibly partial) block
        if (pos < preimage_len)
        {
            uint8_t last[16] = {};
            std::memcpy(last, preimage + pos, preimage_len - pos);
            block m;
            std::memcpy(&m, last, sizeof(m));
            block x = h ^ m;
            prp.permute_block(&x, 1);
            h = x ^ h;
        }

        uint32_t h32 = (uint32_t) _mm_cvtsi128_si32(h);
        if (ell < 32)
        {
            h32 &= (uint32_t) ((1ULL << ell) - 1);
        }
        return h32;
    }

    // hex encode / decode helpers (used by Bgi16 KeyType JSON serialization)

    inline constexpr char HEX_CHARS[] = "0123456789abcdef";

    inline uint8_t hexCharToNibble(char c)
    {
        if (c >= '0' && c <= '9')
        {
            return static_cast<uint8_t>(c - '0');
        }
        if (c >= 'A' && c <= 'F')
        {
            return static_cast<uint8_t>(c - 'A' + 10);
        }
        if (c >= 'a' && c <= 'f')
        {
            return static_cast<uint8_t>(c - 'a' + 10);
        }
        throw std::invalid_argument("Invalid hex character");
    }

    inline std::string m128iToHexString(__m128i v)
    {
        std::string hex(32, '0');
        alignas(16) uint8_t bytes[16];
        std::memcpy(bytes, &v, sizeof(bytes));
        for (int i = 0; i < 16; ++i)
        {
            hex[static_cast<size_t>(i) * 2] = HEX_CHARS[bytes[i] >> 4];
            hex[static_cast<size_t>(i) * 2 + 1] = HEX_CHARS[bytes[i] & 0x0F];
        }
        return hex;
    }

    inline std::string bytesToHexString(const uint8_t* b, size_t n)
    {
        std::string hex(n * 2, '0');
        for (size_t i = 0; i < n; ++i)
        {
            hex[i * 2] = HEX_CHARS[b[i] >> 4];
            hex[i * 2 + 1] = HEX_CHARS[b[i] & 0x0F];
        }
        return hex;
    }

    inline __m128i hexStringToM128i(const std::string& hex)
    {
        if (hex.length() != 32)
        {
            throw std::invalid_argument("Hex string length must be exactly 32");
        }
        alignas(16) uint8_t bytes[16];
        for (int i = 0; i < 16; ++i)
        {
            bytes[i] = static_cast<uint8_t>(hexCharToNibble(hex[static_cast<size_t>(i) * 2]) << 4) |
                    hexCharToNibble(hex[static_cast<size_t>(i) * 2 + 1]);
        }
        __m128i result;
        std::memcpy(&result, bytes, sizeof(result));
        return result;
    }

} // namespace scucse::crypto

#endif // !COMMON_HPP
