#ifndef COMMON_HPP
#define COMMON_HPP

#include <cstdint>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <emp-tool/emp-tool.h>
#include <sodium/randombytes.h>

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

    template <uint64_t ELL> requires(ELL <= 8) uint8_t ringAdd8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) { return static_cast<uint8_t>(a + b); }
        else                    { return static_cast<uint8_t>((a + b) & RING_MASK8<ELL>); }
    }

    template <uint64_t ELL> requires(ELL <= 8) uint8_t ringSub8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) { return static_cast<uint8_t>(a - b); }
        else                    { return static_cast<uint8_t>((a - b) & RING_MASK8<ELL>); }
    }

    template <uint64_t ELL> requires(ELL <= 8) uint8_t ringMul8(uint8_t a, uint8_t b)
    {
        if constexpr (ELL == 8) { return static_cast<uint8_t>(a * b); }
        else                    { return static_cast<uint8_t>((a * b) & RING_MASK8<ELL>); }
    }

    // ———  32-bit (ELL ≤ 32)  ———

    template <uint64_t ELL>
    inline constexpr uint32_t RING_MASK32 =
        (ELL >= 32) ? static_cast<uint32_t>(0xFFFFFFFF)
                    : static_cast<uint32_t>((UINT64_C(1) << ELL) - 1);

    template <uint64_t ELL> requires(ELL <= 32) uint32_t ringAdd32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a + b; }
        return (a + b) & RING_MASK32<ELL>;
    }

    template <uint64_t ELL> requires(ELL <= 32) uint32_t ringSub32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a - b; }
        return (a - b) & RING_MASK32<ELL>;
    }

    template <uint64_t ELL> requires(ELL <= 32) uint32_t ringMul32(uint32_t a, uint32_t b)
    {
        if constexpr (ELL >= 32) { return a * b; }
        return (a * b) & RING_MASK32<ELL>;
    }

} // namespace scucse::crypto::math

namespace scucse::crypto
{

    /// Cryptographically secure random ring element in Z_{2^ell}, 1 ≤ ell ≤ 31.
    inline uint32_t ringRand(uint64_t ell)
    {
        uint32_t v;
        randombytes_buf(&v, sizeof(v));
        if (ell >= 32) return v;
        return v & ((UINT64_C(1) << ell) - 1);
    }

    inline uint32_t hashAESDM
    (
        const uint8_t* preimage,
        size_t preimage_len,
        const uint8_t* key,
        size_t key_len,
        uint64_t ell
    )
    {
        using namespace emp;
        uint8_t pt[16] = {}, k[16] = {};
        size_t n_pt = preimage_len < 16 ? preimage_len : 16;
        size_t n_k = key_len < 16 ? key_len : 16;
        std::memcpy(pt, preimage, n_pt);
        std::memcpy(k, key, n_k);

        block x = _mm_loadu_si128(reinterpret_cast<const __m128i*>(pt));
        block key_block = _mm_loadu_si128(reinterpret_cast<const __m128i*>(k));

        PRP prp(key_block);
        block ct = x;
        prp.permute_block(&ct, 1);
        block h = ct ^ x;

        uint32_t h32 = (uint32_t) _mm_cvtsi128_si32(h);
        if (ell < 32)
        {
            h32 &= (uint32_t) ((1ULL << ell) - 1);
        }
        return h32;
    }

    // — hex encode / decode helpers (used by Bgi16 KeyType JSON serialization) —

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
        uint8_t bytes[16];
        _mm_storeu_si128(reinterpret_cast<__m128i*>(bytes), v);
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
        uint8_t bytes[16];
        for (int i = 0; i < 16; ++i)
        {
            bytes[i] = static_cast<uint8_t>(hexCharToNibble(hex[static_cast<size_t>(i) * 2]) << 4) |
                    hexCharToNibble(hex[static_cast<size_t>(i) * 2 + 1]);
        }
        return _mm_loadu_si128(reinterpret_cast<const __m128i*>(bytes));
    }

} // namespace scucse::crypto

#endif // !COMMON_HPP
