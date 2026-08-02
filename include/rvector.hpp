//  Rvector — compact-packed ring-element vector (ELL bits per element).
//  packRvec / unpackRvec implement the tight packing used by RingTransport.
//
//  SIMD nibble-packing inspired by Daniel Lemire's simdcomp library.
//
//  @author  mincy
//  @ref     https://github.com/lemire/simdcomp
#ifndef RVECTOR_HPP
#define RVECTOR_HPP

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <sodium/randombytes.h>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <immintrin.h>

#include "common.hpp"

namespace scucse::crypto::math
{

/// Minimal aligned allocator for std::vector — ensures cache-line and
/// SIMD alignment required by streaming stores (_mm_stream_si128, etc.).
template <typename T, size_t Alignment>
struct AlignedAlloc
{
    using value_type = T;
    AlignedAlloc() = default;
    template <typename U>
    AlignedAlloc(const AlignedAlloc<U, Alignment>&) noexcept {}
    template <typename U>
    struct rebind { using other = AlignedAlloc<U, Alignment>; };
    [[nodiscard]] T* allocate(size_t n)
    {
        if (n == 0) return nullptr;
        // C17 requires size to be a multiple of alignment
        constexpr size_t mask = Alignment - 1;
        size_t bytes = (n * sizeof(T) + mask) & ~mask;
        void* p = std::aligned_alloc(Alignment, bytes);
        if (!p) throw std::bad_alloc();
        return static_cast<T*>(p);
    }
    void deallocate(T* p, size_t) noexcept { std::free(p); }
};
template <typename T, typename U, size_t A>
bool operator==(const AlignedAlloc<T, A>&, const AlignedAlloc<U, A>&) noexcept
{
    return true;
}
template <typename T, typename U, size_t A>
bool operator!=(const AlignedAlloc<T, A>&, const AlignedAlloc<U, A>&) noexcept
{
    return false;
}

    // SIMD pack/unpack kernels — forward declarations
    /// Optimised pack/unpack for large vectors.
    ///
    /// ELL=4:  _mm_maddubs_epi16 nibble-packing (5 instr / 32 input bytes).
    /// ELL=2:  compiler-friendly heavily-unrolled scalar (auto-vectorises).
    ///
    /// Inspired by Daniel Lemire's simdcomp library
    /// (https://github.com/lemire/simdcomp)

    // ====================================================================
    //  ELL = 4  pack  —  128-bit maddubs × 2  per 32 input bytes
    //  6 instructions / 32 bytes → 16 packed bytes
    // ====================================================================
    inline size_t _pack4(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        const __m128i mul   = _mm_set1_epi32(0x10011001);
        const __m128i mask4 = _mm_set1_epi8(0x0F);
        size_t i = 0, out = 0;

        for (; i + 127 < n; i += 128) {
            for (int k = 0; k < 2; ++k) {
                size_t off = i + k * 64;
                for (int h = 0; h < 2; ++h, out += 16) {
                    size_t o = off + h * 32;
                    __m128i lo = _mm_loadu_si128((const __m128i*)(src + o));
                    __m128i hi = _mm_loadu_si128((const __m128i*)(src + o + 16));
                    lo = _mm_and_si128(lo, mask4);
                    hi = _mm_and_si128(hi, mask4);
                    __m128i p_lo = _mm_maddubs_epi16(mul, lo);
                    __m128i p_hi = _mm_maddubs_epi16(mul, hi);
                    _mm_stream_si128((__m128i*)(dst + out),
                                    _mm_packus_epi16(p_lo, p_hi));
                }
            }
        }
        for (; i + 1 < n; i += 2)
            dst[out++] = (src[i] & 0x0F) | ((src[i+1] & 0x0F) << 4);
        if (i < n) dst[out++] = src[i] & 0x0F;
        return out;
    }

    // ====================================================================
    //  ELL = 4  unpack  —  _mm_cvtepu8_epi16 + interleave + streaming store
    //
    //  8 packed bytes → 16 output bytes via widen + interleave
    // ====================================================================
    inline void _unpack4(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        const __m128i m4 = _mm_set1_epi16(0x0F);
        size_t i = 0, in = 0;

        for (; i + 127 < n; i += 128) {
            for (int k = 0; k < 8; ++k, in += 8) {
                __m128i p = _mm_loadl_epi64((const __m128i*)(src + in));
                // p[0..7] = [e0|e1<<4, e2|e3<<4, …]

                // Widen to 16-bit
                __m128i w = _mm_cvtepu8_epi16(p);  // 8 × 16-bit
                __m128i lo = _mm_and_si128(w, m4);           // [e0,e2,…,e14]
                __m128i hi = _mm_and_si128(_mm_srli_epi16(w, 4), m4); // [e1,e3,…,e15]

                // Interleave: lo×hi → [e0,e1,e2,e3,…]
                __m128i r = _mm_packus_epi16(
                    _mm_unpacklo_epi16(lo, hi),
                    _mm_unpackhi_epi16(lo, hi));

                _mm_stream_si128((__m128i*)(dst + i + k * 16), r);
            }
        }
        for (; i + 1 < n; i += 2, in++)
            { dst[i]=src[in]&0xF; dst[i+1]=src[in]>>4; }
        if (i < n) dst[i]=src[in]&0xF;
    }

    // ====================================================================
    //  ELL = 2  pack  —  _mm_maddubs_epi16 + _mm_hadd_epi16 + PSHUFB
    //                     + streaming stores
    //
    //  16 input bytes → 4 packed bytes, batched ×4 → 16 bytes streamed
    // ====================================================================
    inline size_t _pack2(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        const __m128i mul = _mm_set1_epi32(0x40100401);
        const __m128i m2  = _mm_set1_epi8(3);
        const __m128i sel = _mm_set_epi8(
            0x80,0x80,0x80,0x80, 0x80,0x80,0x80,0x80,
            0x80,0x80,0x80,0x80, 12,8,4,0);
        size_t i = 0, out = 0;

        // 64 input elements → 16 packed bytes, streamed
        for (; i + 255 < n; i += 256) {
            for (int b = 0; b < 4; ++b, out += 16) {
                alignas(16) uint8_t buf[16];
                for (int k = 0; k < 4; ++k) {
                    __m128i v = _mm_loadu_si128(
                        (const __m128i*)(src + i + b * 64 + k * 16));
                    v = _mm_and_si128(v, m2);
                    __m128i p = _mm_maddubs_epi16(mul, v);
                    // _mm_madd_epi16: signed 16×16→32 + horizontal add pairs
                    // a=[1,1,…], b=p → r[i] = p[2i]*1 + p[2i+1]*1
                    // This produces [p0+p1, p2+p3, p4+p5, p6+p7] in order
                    __m128i q = _mm_madd_epi16(p, _mm_set1_epi16(1));
                    __m128i r = _mm_shuffle_epi8(q, sel);
                    buf[k*4]   = ((uint8_t*)&r)[0];
                    buf[k*4+1] = ((uint8_t*)&r)[1];
                    buf[k*4+2] = ((uint8_t*)&r)[2];
                    buf[k*4+3] = ((uint8_t*)&r)[3];
                }
                _mm_stream_si128((__m128i*)(dst + out),
                                _mm_load_si128((__m128i*)buf));
            }
        }
        for (; i + 3 < n; i += 4)
            dst[out++] = (src[i] & 3) | ((src[i+1] & 3) << 2)
                    | ((src[i+2] & 3) << 4) | ((src[i+3] & 3) << 6);
        if (i < n) {
            uint8_t b = 0;
            for (size_t j = 0; i + j < n; ++j)
                b |= (src[i+j] & 3) << (j * 2);
            dst[out++] = b;
        }
        return out;
    }

    // ====================================================================
    //  ELL = 2  unpack  —  unrolled scalar, writes batched via streaming store
    // ====================================================================
    inline void _unpack2(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        size_t i = 0, in = 0;
        for (; i + 255 < n; i += 256) {
            for (int k = 0; k < 16; ++k, in += 4) {
                uint8_t b0=src[in], b1=src[in+1], b2=src[in+2], b3=src[in+3];
                size_t o = i + k * 16;
                // Fill aligned stack buffer then stream
                alignas(16) uint8_t buf[16];
                buf[ 0] = b0 & 3;      buf[ 1] = (b0>>2) & 3;
                buf[ 2] = (b0 >> 4) & 3; buf[ 3] = b0 >> 6;
                buf[ 4] = b1 & 3;      buf[ 5] = (b1>>2) & 3;
                buf[ 6] = (b1>>4) & 3; buf[ 7] = b1 >> 6;
                buf[ 8] = b2 & 3;      buf[ 9] = (b2>>2) & 3;
                buf[10] = (b2>>4) & 3; buf[11] = b2 >> 6;
                buf[12] = b3 & 3;      buf[13] = (b3>>2) & 3;
                buf[14] = (b3>>4) & 3; buf[15] = b3 >> 6;
                _mm_stream_si128((__m128i*)(dst + o), _mm_load_si128((__m128i*)buf));
            }
        }
        for (; i + 3 < n; i += 4, in++) {
            uint8_t b = src[in];
            dst[i]=b&3; dst[i+1]=(b>>2)&3; dst[i+2]=(b>>4)&3; dst[i+3]=b>>6;
        }
        if (i < n) {
            uint8_t b = src[in];
            for (size_t j = 0; i + j < n; ++j)
                dst[i + j] = (b >> (j * 2)) & 3;
        }
    }

    //  Branchless bit-window pack/unpack  (ELL = 3, 5, 6, 7)
    //
    //  Load N packed bytes into a 32/64-bit word, extract all values
    //  with compile-time shift+mask — no branches, no bit-accumulator.
    //  Group size = lcm(ELL, 8) / 8 elements per group.

    // ——— ELL=3: 8 elements  3 bytes ———
    inline size_t _pack3(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 7;
        size_t i = 0, out = 0;
        for (; i + 7 < n; i += 8, out += 3) {
            uint32_t bits = ((uint32_t)(src[i]   & M) <<  0)
                        | ((uint32_t)(src[i+1] & M) <<  3)
                        | ((uint32_t)(src[i+2] & M) <<  6)
                        | ((uint32_t)(src[i+3] & M) <<  9)
                        | ((uint32_t)(src[i+4] & M) << 12)
                        | ((uint32_t)(src[i+5] & M) << 15)
                        | ((uint32_t)(src[i+6] & M) << 18)
                        | ((uint32_t)(src[i+7] & M) << 21);
            dst[out]   = (uint8_t)bits;
            dst[out+1] = (uint8_t)(bits >> 8);
            dst[out+2] = (uint8_t)(bits >> 16);
        }
        if (i < n) {
            uint32_t bits = 0;
            for (size_t j = 0; i + j < n; ++j)
                bits |= ((uint32_t)(src[i+j] & M)) << (j * 3);
            size_t rem_bytes = (n - i) * 3;
            for (size_t b = 0; b < (rem_bytes + 7) / 8; ++b)
                dst[out++] = (uint8_t)(bits >> (b * 8));
        }
        return out;
    }

    inline void _unpack3(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 7;
        size_t i = 0, in = 0;
        for (; i + 7 < n; i += 8, in += 3) {
            uint32_t bits = (uint32_t)src[in]
                        | ((uint32_t)src[in+1] << 8)
                        | ((uint32_t)src[in+2] << 16);
            dst[i]   = (bits >>  0) & M;
            dst[i+1] = (bits >>  3) & M;
            dst[i+2] = (bits >>  6) & M;
            dst[i+3] = (bits >>  9) & M;
            dst[i+4] = (bits >> 12) & M;
            dst[i+5] = (bits >> 15) & M;
            dst[i+6] = (bits >> 18) & M;
            dst[i+7] = (bits >> 21) & M;
        }
        size_t bit = i * 3;
        for (; i < n; ++i, bit += 3)
        {
            dst[i] = (src[bit/8] >> (bit%8)) & M;
            if (bit%8 + 3 > 8)
                dst[i] |= (src[bit/8+1] << (8 - bit%8)) & M;
        }
    }

    // ——— ELL=5: 8 elements ↔ 5 bytes ———
    inline size_t _pack5(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 31;
        size_t i = 0, out = 0;
        for (; i + 7 < n; i += 8, out += 5) {
            uint64_t bits = ((uint64_t)(src[i]   & M) <<  0)
                        | ((uint64_t)(src[i+1] & M) <<  5)
                        | ((uint64_t)(src[i+2] & M) << 10)
                        | ((uint64_t)(src[i+3] & M) << 15)
                        | ((uint64_t)(src[i+4] & M) << 20)
                        | ((uint64_t)(src[i+5] & M) << 25)
                        | ((uint64_t)(src[i+6] & M) << 30)
                        | ((uint64_t)(src[i+7] & M) << 35);
            dst[out]   = (uint8_t)bits;
            dst[out+1] = (uint8_t)(bits >> 8);
            dst[out+2] = (uint8_t)(bits >> 16);
            dst[out+3] = (uint8_t)(bits >> 24);
            dst[out+4] = (uint8_t)(bits >> 32);
        }
        if (i < n) {
            uint64_t bits = 0;
            for (size_t j = 0; i + j < n; ++j)
                bits |= ((uint64_t)(src[i+j] & M)) << (j * 5);
            size_t rem_bytes = (n - i) * 5;
            for (size_t b = 0; b < (rem_bytes + 7) / 8; ++b)
                dst[out++] = (uint8_t)(bits >> (b * 8));
        }
        return out;
    }

    inline void _unpack5(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 31;
        size_t i = 0, in = 0;
        for (; i + 7 < n; i += 8, in += 5) {
            uint64_t bits = (uint64_t)src[in]
                        | ((uint64_t)src[in+1] << 8)
                        | ((uint64_t)src[in+2] << 16)
                        | ((uint64_t)src[in+3] << 24)
                        | ((uint64_t)src[in+4] << 32);
            dst[i]   = (bits >>  0) & M;
            dst[i+1] = (bits >>  5) & M;
            dst[i+2] = (bits >> 10) & M;
            dst[i+3] = (bits >> 15) & M;
            dst[i+4] = (bits >> 20) & M;
            dst[i+5] = (bits >> 25) & M;
            dst[i+6] = (bits >> 30) & M;
            dst[i+7] = (bits >> 35) & M;
        }
        size_t bit = i * 5;
        for (; i < n; ++i, bit += 5)
        {
            dst[i] = (src[bit/8] >> (bit%8)) & M;
            if (bit%8 + 5 > 8)
                dst[i] |= (src[bit/8+1] << (8 - bit%8)) & M;
        }
    }

    // ——— ELL=6: 4 elements ↔ 3 bytes ———
    inline size_t _pack6(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 63;
        size_t i = 0, out = 0;
        for (; i + 3 < n; i += 4, out += 3) {
            uint32_t bits = ((uint32_t)(src[i]   & M) <<  0)
                        | ((uint32_t)(src[i+1] & M) <<  6)
                        | ((uint32_t)(src[i+2] & M) << 12)
                        | ((uint32_t)(src[i+3] & M) << 18);
            dst[out]   = (uint8_t)bits;
            dst[out+1] = (uint8_t)(bits >> 8);
            dst[out+2] = (uint8_t)(bits >> 16);
        }
        if (i < n) {
            uint32_t bits = 0;
            size_t rem = n - i;
            for (size_t j = 0; j < rem; ++j)
                bits |= ((uint32_t)(src[i+j] & M)) << (j * 6);
            size_t remBytes = (rem * 6 + 7) / 8;
            for (size_t b = 0; b < remBytes; ++b)
                dst[out++] = (uint8_t)(bits >> (b * 8));
        }
        return out;
    }

    inline void _unpack6(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 63;
        size_t i = 0, in = 0;
        for (; i + 3 < n; i += 4, in += 3) {
            uint32_t bits = (uint32_t)src[in]
                        | ((uint32_t)src[in+1] << 8)
                        | ((uint32_t)src[in+2] << 16);
            dst[i]   = (bits >>  0) & M;
            dst[i+1] = (bits >>  6) & M;
            dst[i+2] = (bits >> 12) & M;
            dst[i+3] = (bits >> 18) & M;
        }
        size_t bit = i * 6;
        for (; i < n; ++i, bit += 6)
        {
            dst[i] = (src[bit/8] >> (bit%8)) & M;
            if (bit%8 + 6 > 8)
                dst[i] |= (src[bit/8+1] << (8 - bit%8)) & M;
        }
    }

    // ——— ELL=7: 8 elements ↔ 7 bytes ———
    inline size_t _pack7(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 127;
        size_t i = 0, out = 0;
        for (; i + 7 < n; i += 8, out += 7) {
            uint64_t bits = ((uint64_t)(src[i]   & M) <<  0)
                        | ((uint64_t)(src[i+1] & M) <<  7)
                        | ((uint64_t)(src[i+2] & M) << 14)
                        | ((uint64_t)(src[i+3] & M) << 21)
                        | ((uint64_t)(src[i+4] & M) << 28)
                        | ((uint64_t)(src[i+5] & M) << 35)
                        | ((uint64_t)(src[i+6] & M) << 42)
                        | ((uint64_t)(src[i+7] & M) << 49);
            dst[out]   = (uint8_t)bits;
            dst[out+1] = (uint8_t)(bits >> 8);
            dst[out+2] = (uint8_t)(bits >> 16);
            dst[out+3] = (uint8_t)(bits >> 24);
            dst[out+4] = (uint8_t)(bits >> 32);
            dst[out+5] = (uint8_t)(bits >> 40);
            dst[out+6] = (uint8_t)(bits >> 48);
        }
        if (i < n) {
            uint64_t bits = 0;
            for (size_t j = 0; i + j < n; ++j)
                bits |= ((uint64_t)(src[i+j] & M)) << (j * 7);
            size_t rem_bytes = (n - i) * 7;
            for (size_t b = 0; b < (rem_bytes + 7) / 8; ++b)
                dst[out++] = (uint8_t)(bits >> (b * 8));
        }
        return out;
    }

    inline void _unpack7(const uint8_t* __restrict__ src, size_t n,
                            uint8_t* __restrict__ dst) {
        constexpr uint8_t M = 127;
        size_t i = 0, in = 0;
        for (; i + 7 < n; i += 8, in += 7) {
            uint64_t bits = (uint64_t)src[in]
                        | ((uint64_t)src[in+1] << 8)
                        | ((uint64_t)src[in+2] << 16)
                        | ((uint64_t)src[in+3] << 24)
                        | ((uint64_t)src[in+4] << 32)
                        | ((uint64_t)src[in+5] << 40)
                        | ((uint64_t)src[in+6] << 48);
            dst[i]   = (bits >>  0) & M;
            dst[i+1] = (bits >>  7) & M;
            dst[i+2] = (bits >> 14) & M;
            dst[i+3] = (bits >> 21) & M;
            dst[i+4] = (bits >> 28) & M;
            dst[i+5] = (bits >> 35) & M;
            dst[i+6] = (bits >> 42) & M;
            dst[i+7] = (bits >> 49) & M;
        }
        size_t bit = i * 7;
        for (; i < n; ++i, bit += 7)
        {
            dst[i] = (src[bit/8] >> (bit%8)) & M;
            if (bit%8 + 7 > 8)
                dst[i] |= (src[bit/8+1] << (8 - bit%8)) & M;
        }
    }


    /// @brief Pre-allocated packed-byte buffer — works for all ELL 1‥8.
    /// The internal buffer is 16-byte aligned for SIMD streaming stores.
    struct RvectorPack
    {
        static constexpr size_t PACK_ALIGN = 16;
        using BufType = std::vector<uint8_t, AlignedAlloc<uint8_t, PACK_ALIGN>>;

        RvectorPack(uint64_t ell, size_t n) : ell_(ell), n_(n), buf_(_bytesFor(ell, n))
        {
            if (ell < 1 || ell > 8)
                throw std::invalid_argument("RvectorPack: ell must be in [1, 8]");
        }

        const uint8_t* data() const noexcept { return buf_.data(); }
        uint8_t*       data()       noexcept { return buf_.data(); }
        size_t         size() const noexcept { return buf_.size(); }
        uint64_t       ell()  const noexcept { return ell_; }
        size_t         nElements()  const noexcept { return n_; }

    private:
        template <uint64_t> friend class Rvector;
        static size_t _bytesFor(uint64_t ell, size_t n)
        {
            if (ell < 1 || ell > 8)
                throw std::invalid_argument("RvectorPack: ell must be in [1, 8]");
            if (n > SIZE_MAX / 8)
                throw std::overflow_error("RvectorPack: n too large");
            if (ell == 1) return ((n + 63) / 64) * 8;
            if (ell == 8) return n;
            if (n > SIZE_MAX / ell)
                throw std::overflow_error("RvectorPack: n * ell would overflow");
            return (n * ell + 7) / 8;
        }
        uint64_t ell_;
        size_t n_;
        BufType buf_;
    };

    // forward declarations
    template <uint64_t ELL> class Rvector;
    template <uint64_t ELL> inline void packRvec(const Rvector<ELL>& src, RvectorPack& auxBuf);
    template <uint64_t ELL> inline void unpackRvec(const RvectorPack& auxBuf, Rvector<ELL>& dst);

    /// @brief Ring vector over Z_{2^ELL}, 1 ≤ ELL ≤ 8.
    ///
    /// Storage:
    ///   - ELL = 1   → uint64_t[]  bit-packed  (64 elements / word)
    ///   - ELL ≥ 2   → uint8_t[]   byte-packed (1 element  / byte)
    ///
    /// Memory is 32-byte aligned.  Arithmetic is inlined — clang
    /// auto-vectorizes at -O3 -march=native thanks to the extract+restrict
    /// pattern used in the free-standing kernel functions below.
    template <uint64_t ELL> class Rvector
    {
        static_assert(1 <= ELL && ELL <= 8, "ELL must be in [1, 8]");

    public:
        static constexpr uint8_t PER_BYTE_MASK = RING_MASK8<ELL>;

        /// Number of logical elements per machine word.
        static constexpr size_t ELEMS_PER_WORD = (ELL == 1) ? 64 : 1;

    private:
        using word_t = std::conditional_t<ELL == 1, uint64_t, uint8_t>;

        struct _aligned_free
        {
            void operator()(void* p) const noexcept
            {
                std::free(p);
            }
        };
        std::unique_ptr<word_t[], _aligned_free> data_;
        size_t n_ = 0;
        size_t words_ = 0;

        void maskPartialLastWord()
        {
            if constexpr (ELL != 1)
            {
                return;
            }
            if (n_ == 0)
            {
                return;
            }
            const size_t rem = n_ % 64;
            if (rem == 0)
            {
                return;
            }
            data_[words_ - 1] &= (uint64_t{1} << rem) - 1;
        }

    public:
        /// Bring the vector into canonical form: mask padding bits (ELL=1)
        /// and clear high bits of each element (ELL=2..7).  ELL=8 is a
        /// no-op because every byte is already a valid ring element.
        /// Callers should invoke this after any raw-byte import
        /// (from_bytes, unpack, recv_vector, load).
        void canonicalize()
        {
            if constexpr (ELL == 1)
            {
                maskPartialLastWord();
            }
            else if constexpr (ELL != 8)
            {
                for (size_t i = 0; i < words_; ++i)
                    data_[i] &= PER_BYTE_MASK;
            }
        }

    private:
        static constexpr size_t wordsFor(size_t n)
        {
            return (n + ELEMS_PER_WORD - 1) / ELEMS_PER_WORD;
        }

        void allocData(size_t w)
        {
            if (w > 0)
            {
                constexpr size_t ALIGN = 32;
                size_t bytes = w * sizeof(word_t);
                if (bytes % ALIGN != 0)
                {
                    bytes = (bytes + ALIGN - 1) & ~(ALIGN - 1);
                }
                void* raw = std::aligned_alloc(ALIGN, bytes);
                if (!raw)
                {
                    throw std::bad_alloc();
                }
                data_.reset(static_cast<word_t*>(raw));
                words_ = w;
            }
            else
            {
                data_.reset();
                words_ = 0;
            }
        }

    public:
        Rvector() = default;

        /// @brief Construct an uninitialised vector of @p n elements.
        /// Use fill() to initialise all elements to a known value.
        explicit Rvector(size_t n) : n_(n)
        {
            allocData(wordsFor(n));
            if constexpr (ELL == 1) {
                if (words_ > 0 && n_ % 64 != 0)
                    data_[words_ - 1] = 0;
            }
        }

        /// @brief Fill all elements with @p val (default 0).
        void fill(uint8_t val = 0)
        {
            checkRing(val);
            if (words_ == 0)
            {
                return;
            }
            if constexpr (ELL == 1)
            {
                word_t w = (val & 1) ? ~word_t{0} : word_t{0};
                std::fill_n(data_.get(), words_, w);
            }
            else
            {
                std::fill_n(data_.get(), words_, static_cast<word_t>(val));
            }
            maskPartialLastWord();
        }

        /// @brief Fill in-place with fresh pseudorandom elements.
        void randFill()
        {
            if (n_ == 0)
            {
                return;
            }

            __m128i key;
            randombytes_buf(&key, sizeof(key));
            emp::CCRH prg(key);

            uint8_t* dst = reinterpret_cast<uint8_t*>(data_.get());
            const size_t total = words_ * sizeof(word_t);
            uint64_t ctr = 0;
            size_t offset = 0;

            while (offset + 64 <= total)
            {
                alignas(16) emp::block ids[4], raw[4];
                for (int k = 0; k < 4; ++k)
                {
                    ids[k] = _mm_set_epi64x(0, static_cast<int64_t>(ctr + k));
                }
                prg.H<4>(raw, ids);
                std::memcpy(dst + offset, raw, 64);
                ctr += 4;
                offset += 64;
            }

            if (offset < total)
            {
                alignas(16) emp::block tail[4];
                for (int k = 0; k < 4; ++k)
                {
                    tail[k] = _mm_set_epi64x(0, static_cast<int64_t>(ctr + k));
                }
                prg.H<4>(tail, tail);
                std::memcpy(dst + offset, tail, total - offset);
            }

            if constexpr (ELL != 8 && ELL != 1)
            {
                for (size_t i = 0; i < words_; ++i)
                {
                    data_[i] &= PER_BYTE_MASK;
                }
            }

            if constexpr (ELL == 1)
            {
                maskPartialLastWord();
            }
        }

        Rvector(const Rvector& other) : n_(other.n_)
        {
            if (other.words_ > 0)
            {
                allocData(other.words_);
                std::memcpy(data_.get(), other.data_.get(), words_ * sizeof(word_t));
            }
        }

        Rvector(Rvector&& other) noexcept
            : data_(std::move(other.data_)), n_(other.n_), words_(other.words_)
        {
            other.n_ = 0;
            other.words_ = 0;
        }

        Rvector& operator=(const Rvector& other)
        {
            if (this != &other)
            {
                *this = Rvector(other);
            }
            return *this;
        }

        Rvector& operator=(Rvector&& other) noexcept
        {
            if (this != &other)
            {
                data_ = std::move(other.data_);
                n_ = other.n_;
                words_ = other.words_;
                other.n_ = 0;
                other.words_ = 0;
            }
            return *this;
        }

        ~Rvector() = default;

        size_t size() const noexcept
        {
            return n_;
        }

        size_t words() const noexcept
        {
            return words_;
        }

        const word_t* data() const noexcept
        {
            return data_.get();
        }

        word_t* data() noexcept
        {
            return data_.get();
        }

        const uint8_t* bytes() const noexcept
        {
            return reinterpret_cast<const uint8_t*>(data_.get());
        }

        size_t bytesSize() const noexcept
        {
            return words_ * sizeof(word_t);
        }

        // ——— file I/O ———
        // Format:  [uint32_t magic "MPMT"] [uint64_t ELL] [uint64_t n] [payload]
        // Extension: .mpmtrvp

        static constexpr uint32_t FILE_MAGIC = 0x4D504D54;  // "MPMT"

        static void _checkExt(const std::string& path, const char* ext, const char* op)
        {
            size_t elen = std::strlen(ext);
            if (path.size() < elen || path.compare(path.size() - elen, elen, ext) != 0)
                throw std::runtime_error(
                    std::string(op) + ": file extension must be " + ext + ": " + path);
        }

        void save(const std::string& path, RvectorPack& auxBuf) const
        {
            _checkExt(path, ".mpmtrvp", "save");
            auto f = std::unique_ptr<FILE, decltype(&fclose)>(
                fopen(path.c_str(), "wb"), &fclose);
            if (!f) throw std::runtime_error("save: cannot open " + path);
            if (fwrite(&FILE_MAGIC, sizeof(uint32_t), 1, f.get()) != 1)
                throw std::runtime_error("save: failed to write magic");
            uint64_t hdr[2] = {ELL, n_};
            if (fwrite(hdr, sizeof(uint64_t), 2, f.get()) != 2)
                throw std::runtime_error("save: failed to write header");
            packRvec(*this, auxBuf);
            if (fwrite(auxBuf.data(), 1, auxBuf.size(), f.get()) != auxBuf.size())
                throw std::runtime_error("save: failed to write payload");
        }

        void load(const std::string& path, RvectorPack& auxBuf)
        {
            _checkExt(path, ".mpmtrvp", "load");
            auto f = std::unique_ptr<FILE, decltype(&fclose)>(
                fopen(path.c_str(), "rb"), &fclose);
            if (!f) throw std::runtime_error("load: cannot open " + path);

            // read and validate magic
            uint32_t magic = 0;
            if (fread(&magic, sizeof(uint32_t), 1, f.get()) != 1 || magic != FILE_MAGIC)
                throw std::runtime_error("load: not a valid .mpmtrvp file (bad magic)");

            // read header
            uint64_t hdr[2];
            if (fread(hdr, sizeof(uint64_t), 2, f.get()) != 2)
                throw std::runtime_error("load: truncated header in " + path);
            if (hdr[0] != ELL)
                throw std::runtime_error(
                    "load: ELL mismatch, file=" + std::to_string(hdr[0])
                    + " expected=" + std::to_string(ELL));
            if (hdr[1] != n_)
                throw std::runtime_error(
                    "load: size mismatch, file=" + std::to_string(hdr[1])
                    + " this=" + std::to_string(n_));

            // verify payload size
            long cur = ftell(f.get());
            fseek(f.get(), 0, SEEK_END);
            long end = ftell(f.get());
            fseek(f.get(), cur, SEEK_SET);
            size_t payloadSize = static_cast<size_t>(end - cur);
            if (payloadSize != auxBuf.size())
                throw std::runtime_error(
                    "load: corrupt file — expected " + std::to_string(auxBuf.size())
                    + " bytes payload, got " + std::to_string(payloadSize));

            if (fread(auxBuf.data(), 1, auxBuf.size(), f.get()) != auxBuf.size())
                throw std::runtime_error("load: truncated payload in " + path);
            unpackRvec(auxBuf, *this);
            if constexpr (ELL == 1) maskPartialLastWord();
        }

    public:

        /// @brief Get the element at index @p i.
        /// @throws std::out_of_range if @p i exceeds the vector size.
        uint8_t get(size_t i) const
        {
            if (i >= n_)
                throw std::out_of_range(
                    "Rvector::get: index " + std::to_string(i)
                    + " out of range [0, " + std::to_string(n_) + ")");
            if constexpr (ELL == 1)
            {
                return (data_[i / 64] >> (i % 64)) & 1;
            }
            else
            {
                return data_[i] & PER_BYTE_MASK;
            }
        }

        /// @brief Set the element at index @p i to @p val.
        /// @throws std::out_of_range if @p i exceeds the vector size.
        void set(size_t i, uint8_t val)
        {
            if (i >= n_)
                throw std::out_of_range(
                    "Rvector::set: index " + std::to_string(i)
                    + " out of range [0, " + std::to_string(n_) + ")");
            if constexpr (ELL == 1)
            {
                if (val & 1)
                {
                    data_[i / 64] |= (uint64_t{1} << (i % 64));
                }
                else
                {
                    data_[i / 64] &= ~(uint64_t{1} << (i % 64));
                }
            }
            else
            {
                data_[i] = val & PER_BYTE_MASK;
            }
        }

        void checkRing(uint8_t val) const
        {
            if constexpr (ELL < 8)
            {
                if (val > PER_BYTE_MASK)
                {
                    throw std::invalid_argument(
                        "Rvector<" + std::to_string(ELL) + ">: value " + std::to_string(val) +
                        " exceeds ring modulus 2^" + std::to_string(ELL)
                    );
                }
            }
        }

        /// @brief Batch-set: set elements at @p indices[0..n) to @p val.
        void batchSet(const uint64_t* indices, size_t n, uint8_t val)
        {
            checkRing(val);
            for (size_t k = 0; k < n; ++k)
            {
                const uint64_t i = indices[k];
                if (i >= n_)
                    throw std::out_of_range("batchSet: index " + std::to_string(i) + " >= " + std::to_string(n_));
                set(i, val);
            }
        }

        /// @brief Batch-get: write elements at @p indices[0..n) into @p out[0..n).
        void batchGet(const uint64_t* indices, size_t n, Rvector& out) const
        {
            if (&out == this)
                throw std::invalid_argument("batchGet: output must not alias source");
            if (out.size() < n)
                throw std::invalid_argument("batchGet: out.size() must be >= n");
            for (size_t k = 0; k < n; ++k)
            {
                const uint64_t i = indices[k];
                if (i >= n_)
                    throw std::out_of_range("batchGet: index " + std::to_string(i) + " >= " + std::to_string(n_));
                out.set(k, get(i));
            }
        }

        void checkVecSizes(const Rvector& other) const
        {
            if (n_ != other.n_)
            {
                throw std::logic_error(
                    "Rvector size mismatch: " + std::to_string(n_) + " vs " + std::to_string(other.n_)
                );
            }
        }

        void checkOutSize(const Rvector& out) const
        {
            if (n_ != out.n_)
            {
                throw std::logic_error(
                    "Rvector out-size mismatch: src " + std::to_string(n_) + " vs out " +
                    std::to_string(out.n_)
                );
            }
        }

        /// @name Element-wise arithmetic
        /// @note add / sub support in-place (out may alias a or b).
        ///       hadamard forbids aliasing (out must not alias inputs).
        /// @{

        /// @brief out[i] = (a[i] + b[i]) mod 2^ELL
        static void add(const Rvector& a, const Rvector& b, Rvector& out)
        {
            a.checkVecSizes(b);
            a.checkOutSize(out);
            _add<ELL>(a, b, out);
            out.maskPartialLastWord();
        }

        /// @brief out[i] = (a[i] - b[i]) mod 2^ELL
        static void sub(const Rvector& a, const Rvector& b, Rvector& out)
        {
            a.checkVecSizes(b);
            a.checkOutSize(out);
            _sub<ELL>(a, b, out);
            out.maskPartialLastWord();
        }

        /// @brief out[i] = (a[i] * b[i]) mod 2^ELL
        static void hadamard(const Rvector& a, const Rvector& b, Rvector& out)
        {
            a.checkVecSizes(b);
            a.checkOutSize(out);
            _hadamard<ELL>(a, b, out);
            out.maskPartialLastWord();
        }

        /// @brief out[i] = (a[i] + scalar) mod 2^ELL
        static void addScalar(const Rvector& a, uint8_t scalar, Rvector& out)
        {
            a.checkRing(scalar);
            a.checkOutSize(out);
            _addScalar<ELL>(a, scalar, out);
            out.maskPartialLastWord();
        }

        /// @brief out[i] = (a[i] - scalar) mod 2^ELL
        static void subScalar(const Rvector& a, uint8_t scalar, Rvector& out)
        {
            a.checkRing(scalar);
            a.checkOutSize(out);
            _subScalar<ELL>(a, scalar, out);
            out.maskPartialLastWord();
        }

        /// @brief out[i] = (a[i] * scalar) mod 2^ELL
        static void mulScalar(const Rvector& a, uint8_t scalar, Rvector& out)
        {
            a.checkRing(scalar);
            a.checkOutSize(out);
            _mulScalar<ELL>(a, scalar, out);
            out.maskPartialLastWord();
        }

        /// @}

        /// @brief Σ (a[i] * b[i]) mod 2^ELL
        static uint8_t dot(const Rvector& a, const Rvector& b)
        {
            a.checkVecSizes(b);
            return _dot<ELL>(a, b);
        }

        /// @brief Sum all elements modulo 2^ELL.
        ///
        /// Uses vpsadbw (_mm256_sad_epu8) for fast byte-wise horizontal sum.
        static uint8_t reduce(const Rvector& a)
        {
            if constexpr (ELL == 1)
            {
                uint64_t parity = 0;
                for (size_t i = 0; i < a.words(); ++i)
                {
                    parity += __builtin_popcountll(a.data()[i]);
                }
                return static_cast<uint8_t>(parity & 1);
            }
            uint64_t acc = 0;
            size_t i = 0;
            const size_t n = a.size();
            const __m256i vzero = _mm256_setzero_si256();
            for (; i + 32 <= n; i += 32)
            {
                __m256i va = _mm256_loadu_si256(reinterpret_cast<const __m256i*>(a.data() + i));
                __m256i vsad = _mm256_sad_epu8(va, vzero);
                acc += static_cast<uint64_t>(static_cast<uint16_t>(_mm256_extract_epi64(vsad, 0))) +
                    static_cast<uint64_t>(static_cast<uint16_t>(_mm256_extract_epi64(vsad, 1))) +
                    static_cast<uint64_t>(static_cast<uint16_t>(_mm256_extract_epi64(vsad, 2))) +
                    static_cast<uint64_t>(static_cast<uint16_t>(_mm256_extract_epi64(vsad, 3)));
            }
            for (; i < n; ++i)
            {
                acc += a.data()[i];
            }
            if constexpr (ELL == 8)
            {
                return static_cast<uint8_t>(acc);
            }
            else
            {
                return static_cast<uint8_t>(acc & PER_BYTE_MASK);
            }
        }
    };

    // CRITICAL: extract raw pointers BEFORE the loop with __restrict__,
    // otherwise clang cannot deduce array bounds and falls back to scalar code.

    // Note: out may alias a or b (ABY3 add_vec supports in-place).
    template <uint64_t ELL>
    inline void _add(const Rvector<ELL>& a, const Rvector<ELL>& b, Rvector<ELL>& out)
    {
        const size_t n = a.size();
        if constexpr (ELL == 1)
        {
            const size_t w = a.words();
            const auto* __restrict__ pa = a.data();
            const auto* __restrict__ pb = b.data();
            auto* __restrict__ po = out.data();
            for (size_t i = 0; i < w; ++i)
            {
                po[i] = pa[i] ^ pb[i];
            }
        }
        else
        {
            const auto* __restrict__ pa = a.data();
            const auto* __restrict__ pb = b.data();
            auto* __restrict__ po = out.data();
            if constexpr (ELL == 8)
            {
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = pa[i] + pb[i];
                }
            }
            else
            {
                constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = (pa[i] + pb[i]) & M;
                }
            }
        }
    }

    template <uint64_t ELL>
    inline void _sub(const Rvector<ELL>& a, const Rvector<ELL>& b, Rvector<ELL>& out)
    {
        if constexpr (ELL == 1)
        {
            _add<ELL>(a, b, out);
            return;
        }
        const size_t n = a.size();
        const auto* __restrict__ pa = a.data();
        const auto* __restrict__ pb = b.data();
        auto* __restrict__ po = out.data();
        if constexpr (ELL == 8)
        {
            for (size_t i = 0; i < n; ++i)
            {
                po[i] = pa[i] - pb[i];
            }
        }
        else
        {
            constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
            for (size_t i = 0; i < n; ++i)
            {
                po[i] = (pa[i] - pb[i]) & M;
            }
        }
    }

    template <uint64_t ELL>
    inline void _hadamard(const Rvector<ELL>& a, const Rvector<ELL>& b, Rvector<ELL>& out)
    {
        const size_t n = a.size();
        if constexpr (ELL == 1)
        {
            const size_t w = a.words();
            const auto* __restrict__ pa = a.data();
            const auto* __restrict__ pb = b.data();
            auto* __restrict__ po = out.data();
            for (size_t i = 0; i < w; ++i)
            {
                po[i] = pa[i] & pb[i];
            }
        }
        else
        {
            const auto* __restrict__ pa = a.data();
            const auto* __restrict__ pb = b.data();
            auto* __restrict__ po = out.data();
            if constexpr (ELL == 8)
            {
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = static_cast<uint8_t>(pa[i] * pb[i]);
                }
            }
            else
            {
                constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = static_cast<uint8_t>((pa[i] * pb[i]) & M);
                }
            }
        }
    }

    template <uint64_t ELL>
    inline void _addScalar(const Rvector<ELL>& a, uint8_t scalar, Rvector<ELL>& out)
    {
        const size_t n = a.size();
        if constexpr (ELL == 1)
        {
            const size_t w = a.words();
            const auto* __restrict__ pa = a.data();
            auto* __restrict__ po = out.data();
            if (scalar == 0)
            {
                for (size_t i = 0; i < w; ++i)
                {
                    po[i] = pa[i];
                }
            }
            else
            {
                for (size_t i = 0; i < w; ++i)
                {
                    po[i] = ~pa[i];
                }
            }
        }
        else
        {
            const auto* __restrict__ pa = a.data();
            auto* __restrict__ po = out.data();
            if constexpr (ELL == 8)
            {
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = pa[i] + scalar;
                }
            }
            else
            {
                constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = (pa[i] + scalar) & M;
                }
            }
        }
    }

    template <uint64_t ELL>
    inline void _subScalar(const Rvector<ELL>& a, uint8_t scalar, Rvector<ELL>& out)
    {
        if constexpr (ELL == 1)
        {
            _addScalar<ELL>(a, scalar, out);
            return;
        }
        const size_t n = a.size();
        const auto* __restrict__ pa = a.data();
        auto* __restrict__ po = out.data();
        if constexpr (ELL == 8)
        {
            for (size_t i = 0; i < n; ++i)
            {
                po[i] = pa[i] - scalar;
            }
        }
        else
        {
            constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
            for (size_t i = 0; i < n; ++i)
            {
                po[i] = (pa[i] - scalar) & M;
            }
        }
    }

    template <uint64_t ELL>
    inline void _mulScalar(const Rvector<ELL>& a, uint8_t scalar, Rvector<ELL>& out)
    {
        const size_t n = a.size();
        if constexpr (ELL == 1)
        {
            const size_t w = a.words();
            const auto* __restrict__ pa = a.data();
            auto* __restrict__ po = out.data();
            if (scalar == 1)
            {
                for (size_t i = 0; i < w; ++i)
                {
                    po[i] = pa[i];
                }
            }
            else
            {
                for (size_t i = 0; i < w; ++i)
                {
                    po[i] = 0;
                }
            }
        }
        else
        {
            const auto* __restrict__ pa = a.data();
            auto* __restrict__ po = out.data();
            if constexpr (ELL == 8)
            {
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = static_cast<uint8_t>(pa[i] * scalar);
                }
            }
            else
            {
                constexpr uint8_t M = Rvector<ELL>::PER_BYTE_MASK;
                for (size_t i = 0; i < n; ++i)
                {
                    po[i] = static_cast<uint8_t>((pa[i] * scalar) & M);
                }
            }
        }
    }

    template <uint64_t ELL> inline uint8_t _dot(const Rvector<ELL>& a, const Rvector<ELL>& b)
    {
        const size_t n = a.size();
        if constexpr (ELL == 1)
        {
            uint64_t acc = 0;
            const size_t w = a.words();
            const auto* __restrict__ pa = a.data();
            const auto* __restrict__ pb = b.data();
            for (size_t i = 0; i < w; ++i)
            {
                acc += __builtin_popcountll(pa[i] & pb[i]);
            }
            return acc & 1;
        }
        uint64_t acc = 0;
        const auto* __restrict__ pa = a.data();
        const auto* __restrict__ pb = b.data();
        for (size_t i = 0; i < n; ++i)
        {
            acc += static_cast<uint64_t>(static_cast<uint16_t>(pa[i]) * static_cast<uint16_t>(pb[i]));
        }
        if constexpr (ELL == 8)
        {
            return static_cast<uint8_t>(acc);
        }
        else
        {
            return static_cast<uint8_t>(acc & Rvector<ELL>::PER_BYTE_MASK);
        }
    }

    // ——— pack / unpack free functions ———

    template <uint64_t ELL>
    inline void packRvec(const Rvector<ELL>& src, RvectorPack& auxBuf)
    {
        if (auxBuf.ell() != ELL || auxBuf.nElements() != src.size())
            throw std::invalid_argument("packRvec: auxBuf ell/n mismatch");
        const uint8_t* s = src.bytes();
        if constexpr (ELL == 1 || ELL == 8) {
            if (src.bytesSize() > 0)
                std::memcpy(auxBuf.data(), s, src.bytesSize());
        }
        else if constexpr (ELL == 2)      _pack2(s, src.size(), auxBuf.data());
        else if constexpr (ELL == 3)      _pack3(s, src.size(), auxBuf.data());
        else if constexpr (ELL == 4)      _pack4(s, src.size(), auxBuf.data());
        else if constexpr (ELL == 5)      _pack5(s, src.size(), auxBuf.data());
        else if constexpr (ELL == 6)      _pack6(s, src.size(), auxBuf.data());
        else if constexpr (ELL == 7)      _pack7(s, src.size(), auxBuf.data());
    }

    template <uint64_t ELL>
    inline void unpackRvec(const RvectorPack& auxBuf, Rvector<ELL>& dst)
    {
        if (auxBuf.ell() != ELL || auxBuf.nElements() != dst.size())
            throw std::invalid_argument("unpackRvec: auxBuf ell/n mismatch");
        if constexpr (ELL == 1 || ELL == 8) {
            if (dst.bytesSize() > 0)
                std::memcpy(dst.data(), auxBuf.data(), std::min(auxBuf.size(), dst.bytesSize()));
        }
        else if constexpr (ELL == 2)      _unpack2(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        else if constexpr (ELL == 3)      _unpack3(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        else if constexpr (ELL == 4)      _unpack4(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        else if constexpr (ELL == 5)      _unpack5(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        else if constexpr (ELL == 6)      _unpack6(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        else if constexpr (ELL == 7)      _unpack7(auxBuf.data(), dst.size(), reinterpret_cast<uint8_t*>(dst.data()));
        dst.canonicalize();
    }

} // namespace scucse::crypto::math
#endif // RVECTOR_HPP
