//  ABY3 — 3-party replicated secret sharing (RSS3).
//
//  @author  mincy
//  @ref     ABY3 (Mohassel et al.), adapted for vectorized Rvector
#ifndef ABY3_HPP
#define ABY3_HPP

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <exception>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <immintrin.h>
#include <sodium.h>
#include <emp-tool/emp-tool.h>

#include "common.hpp"
#include "ideal_functionality/shr_rep3.hpp"
#include "rvector.hpp"

namespace scucse::crypto
{

/// @brief 3-party replicated secret sharing (ABY3).
/// @warning This class is NOT thread-safe. A single instance must not be
///          shared across threads — all protocol operations (share, mul,
///          hadamard, dot, ringConv, etc.) share CRNG state internally.
template <ShrRep3Pid PID, uint64_t ELL, template <uint64_t> typename RVECTOR>
class Aby3
{
public:
    using ShareScalar = ShrRep3ShareScalar;

    template <uint64_t ELL_T> using ShareVector = ShrRep3ShareVec<ELL_T, RVECTOR>;

    static constexpr ShrRep3Pid PREV = PID == ShrRep3Pid::P0   ? ShrRep3Pid::P2
                                       : PID == ShrRep3Pid::P1 ? ShrRep3Pid::P0
                                                               : ShrRep3Pid::P1;

    static constexpr ShrRep3Pid NEXT = PID == ShrRep3Pid::P0   ? ShrRep3Pid::P1
                                       : PID == ShrRep3Pid::P1 ? ShrRep3Pid::P2
                                                               : ShrRep3Pid::P0;

    /// @brief Constructor — generates a random PRF key and exchanges it
    ///        over the ring (send to NEXT, recv from PREV).
    ///        Takes non-owning raw pointers; the caller owns the IOChannel lifetime.
    Aby3(emp::IOChannel* nioToPrev, emp::IOChannel* nioToNext)
        : nioToPrev_(nioToPrev), nioToNext_(nioToNext),
          cRngId_(_mm_setzero_si128()),
          crngPtr_(BLOCK_CAPACITY * 8) // start exhausted, forces refill on first use
    {
        if (sodium_init() < 0)
        {
            throw std::runtime_error("libsodium init failed.");
        }

        __m128i keyThis, keyPrev;
        randombytes_buf(&keyThis, sizeof(keyThis));
        nioToNext_->send_data(reinterpret_cast<const uint8_t*>(&keyThis), sizeof(keyThis));
        nioToNext_->flush();
        nioToPrev_->recv_data(reinterpret_cast<uint8_t*>(&keyPrev), sizeof(keyPrev));

        ccrhThis_ = emp::CCRH(keyThis);
        ccrhPrev_ = emp::CCRH(keyPrev);

        // Start persistent I/O workers for parallel send+recv in _reshare_ring
        sendWorker_ = std::thread(&Aby3::sendLoop, this);
        recvWorker_ = std::thread(&Aby3::recvLoop, this);
    }

    ~Aby3()
    {
        shutdown();
    }

    /// Shut down the persistent I/O worker threads.
    void shutdown()
    {
        if (shutdown_) return;

        // Wake workers and tell them to exit
        {
            std::lock_guard lk(sendMtx_);
            shutdown_ = true;
            sendReady_ = true;
        }
        sendCv_.notify_one();

        {
            std::lock_guard lk(recvMtx_);
            recvReady_ = true;
        }
        recvCv_.notify_one();

        if (sendWorker_.joinable()) sendWorker_.join();
        if (recvWorker_.joinable()) recvWorker_.join();
    }

    /// Flush both underlying IOChannels.
    void flush()
    {
        nioToPrev_->flush();
        nioToNext_->flush();
    }

    /// Chunked ring reshare — send *sendBuf* to PREV, recv into *recvBuf*
    /// from NEXT.  64 KiB chunks, send and recv run in parallel via
    /// persistent worker threads created at construction.
    void _reshare_ring(const void* sendBuf, void* recvBuf, size_t totalBytes)
    {
        // Set up work for both workers
        {
            std::lock_guard lk(sendMtx_);
            sendBuf_ = static_cast<const uint8_t*>(sendBuf);
            sendTotal_ = totalBytes;
            sendReady_ = true;
        }
        sendCv_.notify_one();

        {
            std::lock_guard lk(recvMtx_);
            recvBuf_ = static_cast<uint8_t*>(recvBuf);
            recvTotal_ = totalBytes;
            recvReady_ = true;
        }
        recvCv_.notify_one();

        // Wait for both to finish, then check errors
        {
            std::unique_lock lk(sendMtx_);
            sendCv_.wait(lk, [this] { return sendDone_; });
            sendDone_ = false;
        }
        {
            std::unique_lock lk(recvMtx_);
            recvCv_.wait(lk, [this] { return recvDone_; });
            recvDone_ = false;
        }

        // Re-throw any worker error
        {
            std::lock_guard lk(errMtx_);
            if (sendErr_) std::rethrow_exception(sendErr_);
            if (recvErr_) std::rethrow_exception(recvErr_);
        }
    }

    ShareScalar share(const uint8_t num)
    {
        ShareScalar s;

        // 3-out-of-3 share.
        s.thisShare = math::ringAdd8<ELL>(num, crng<ELL>());

        // reshare and get rss.
        nioToPrev_->send_data(&s.thisShare, 1);
        nioToPrev_->flush();
        nioToNext_->recv_data(&s.nxtShare, 1);

        return s;
    }

    /// @pre  &vec != &oVec.thisShare
    void share(const RVECTOR<ELL>& vec, ShareVector<ELL>& oVec)
    {
        if (&vec == &oVec.thisShare)
        {
            throw std::invalid_argument("share: vec must not alias oVec.thisShare");
        }

        // 3-out-of-3 share: oVec.thisShare = vec + crng
        crng<ELL>(oVec.thisShare);
        RVECTOR<ELL>::add(vec, oVec.thisShare, oVec.thisShare);

        // Reshare: send thisShare to prev, recv nxtShare from next
        _reshare_ring(oVec.thisShare.data(), oVec.nxtShare.data(),
                      oVec.nxtShare.bytesSize());
    }

    ShareScalar recvShare()
    {
        ShareScalar s;

        // 3-out-of-3 share.
        s.thisShare = crng<ELL>();

        // reshare and get rss.
        nioToPrev_->send_data(&s.thisShare, 1);
        nioToPrev_->flush();
        nioToNext_->recv_data(&s.nxtShare, 1);

        return s;
    }

    void recvShare(ShareVector<ELL>& oVec)
    {
        // 3-out-of-3 share: thisShare = crng
        crng<ELL>(oVec.thisShare);

        // Reshare: send thisShare to prev, recv nxtShare from next
        _reshare_ring(oVec.thisShare.data(), oVec.nxtShare.data(),
                      oVec.nxtShare.bytesSize());
    }

    /// Re-share an existing additive-share component into RSS3 form.
    /// The caller already holds its additive share in *vec*;
    /// this method sends it around the ring and fills *oVec*.
    /// @pre  &vec != &oVec.thisShare
    void reshare(const RVECTOR<ELL>& vec, ShareVector<ELL>& oVec)
    {
        if (&vec == &oVec.thisShare)
        {
            throw std::invalid_argument("reshare: vec must not alias oVec.thisShare");
        }

        // Use caller-provided additive share as thisShare
        oVec.thisShare = vec;

        // Reshare: send thisShare to prev, recv nxtShare from next
        _reshare_ring(oVec.thisShare.data(), oVec.nxtShare.data(),
                      oVec.nxtShare.bytesSize());
    }

    /// Scalar variant: reshare a single byte into RSS3 form.
    ShareScalar reshare(uint8_t val)
    {
        ShareScalar s;
        s.thisShare = val;

        nioToPrev_->send_data(&s.thisShare, 1);
        nioToPrev_->flush();
        nioToNext_->recv_data(&s.nxtShare, 1);

        return s;
    }

    // ——— send / recv (plain data transfer) ———

    /// Send a scalar to party TO.
    template <ShrRep3Pid TO>
        requires(TO != PID)
    void send(uint8_t val)
    {
        if constexpr (TO == PREV)
        {
            nioToPrev_->send_data(&val, 1);
            nioToPrev_->flush();
        }
        else
        {
            nioToNext_->send_data(&val, 1);
            nioToNext_->flush();
        }
    }

    /// Send a vector to party TO.
    template <ShrRep3Pid TO>
        requires(TO != PID)
    void send(const RVECTOR<ELL>& vec)
    {
        if constexpr (TO == PREV)
        {
            nioToPrev_->send_data(vec.data(), vec.bytesSize());
            nioToPrev_->flush();
        }
        else
        {
            nioToNext_->send_data(vec.data(), vec.bytesSize());
            nioToNext_->flush();
        }
    }

    /// Receive a scalar from party FROM.
    template <ShrRep3Pid FROM>
        requires(FROM != PID)
    uint8_t recv()
    {
        uint8_t val;
        if constexpr (FROM == PREV)
        {
            nioToPrev_->recv_data(&val, 1);
        }
        else
        {
            nioToNext_->recv_data(&val, 1);
        }
        return val;
    }

    /// Receive a vector from party FROM.  out must be pre-allocated.
    template <ShrRep3Pid FROM>
        requires(FROM != PID)
    void recv(RVECTOR<ELL>& out)
    {
        if constexpr (FROM == PREV)
        {
            nioToPrev_->recv_data(out.data(), out.bytesSize());
        }
        else
        {
            nioToNext_->recv_data(out.data(), out.bytesSize());
        }
    }

    // ———  public raw I/O (replaces friend access)  ———

    void send_data(int to, const void* data, size_t len)
    {
        if (to == static_cast<int>(PREV))
            { nioToPrev_->send_data(data, len); nioToPrev_->flush(); }
        else
            { nioToNext_->send_data(data, len); nioToNext_->flush(); }
    }

    void recv_data(int from, void* data, size_t len)
    {
        if (from == static_cast<int>(PREV))
            nioToPrev_->recv_data(data, len);
        else
            nioToNext_->recv_data(data, len);
    }

    /// Sum of bytes sent over both channels.
    uint64_t bytes_sent() const { return nioToPrev_->send_counter + nioToNext_->send_counter; }
    /// Sum of bytes received over both channels.
    uint64_t bytes_recv() const { return nioToPrev_->recv_counter + nioToNext_->recv_counter; }
    /// Reset sent-byte counters on both channels.
    void clear_send_cnt() { nioToPrev_->send_counter = 0; nioToNext_->send_counter = 0; }
    /// Reset received-byte counters on both channels.
    void clear_recv_cnt() { nioToPrev_->recv_counter = 0; nioToNext_->recv_counter = 0; }

    // ——— revealAll ———
    //
    //  P1 sends its nxtShare (s₂, the share P0 is missing) to P0.
    //  P0 reconstructs s₀ + s₁ + s₂ and broadcasts the plaintext back
    //  to both P1 and P2.  All three parties must call simultaneously.

    uint8_t revealAll(const ShareScalar& ss)
    {
        if constexpr (PID == ShrRep3Pid::P0)
        {
            uint8_t missing;
            nioToNext_->recv_data(&missing, 1);
            uint16_t r = (uint16_t)ss.thisShare + (uint16_t)ss.nxtShare + (uint16_t)missing;
            if constexpr (ELL < 8) r &= math::RING_MASK8<ELL>;
            uint8_t result = (uint8_t)r;
            nioToNext_->send_data(&result, 1);
            nioToNext_->flush();
            nioToPrev_->send_data(&result, 1);
            nioToPrev_->flush();
            return result;
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            nioToPrev_->send_data(&ss.nxtShare, 1);
            nioToPrev_->flush();
            uint8_t result;
            nioToPrev_->recv_data(&result, 1);
            return result;
        }
        else
        {
            uint8_t result;
            nioToNext_->recv_data(&result, 1);
            return result;
        }
    }

    void revealAll(const ShareVector<ELL>& sv, RVECTOR<ELL>& out)
    {
        size_t sz = out.bytesSize();

        if constexpr (PID == ShrRep3Pid::P0)
        {
            nioToNext_->recv_data(out.data(), sz);
            RVECTOR<ELL> tmp(out.size());
            RVECTOR<ELL>::add(sv.thisShare, sv.nxtShare, tmp);
            RVECTOR<ELL>::add(tmp, out, out);
            nioToNext_->send_data(out.data(), sz);
            nioToNext_->flush();
            nioToPrev_->send_data(out.data(), sz);
            nioToPrev_->flush();
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            nioToPrev_->send_data(sv.nxtShare.data(), sz);
            nioToPrev_->flush();
            nioToPrev_->recv_data(out.data(), sz);
        }
        else
        {
            nioToNext_->recv_data(out.data(), sz);
        }
    }

    ShareScalar add(const ShareScalar num1, const ShareScalar num2)
    {
        ShareScalar res;
        res.thisShare = math::ringAdd8<ELL>(num1.thisShare, num2.thisShare);
        res.nxtShare = math::ringAdd8<ELL>(num1.nxtShare, num2.nxtShare);
        return res;
    }

    /// @note  Aliasing safe — oVec may alias iVec1 or iVec2.
    void add(const ShareVector<ELL>& iVec1, const ShareVector<ELL>& iVec2, ShareVector<ELL>& oVec)
    {
        RVECTOR<ELL>::add(iVec1.thisShare, iVec2.thisShare, oVec.thisShare);
        RVECTOR<ELL>::add(iVec1.nxtShare, iVec2.nxtShare, oVec.nxtShare);
    }

    ShareScalar sub(const ShareScalar num1, const ShareScalar num2)
    {
        ShareScalar res;
        res.thisShare = math::ringSub8<ELL>(num1.thisShare, num2.thisShare);
        res.nxtShare = math::ringSub8<ELL>(num1.nxtShare, num2.nxtShare);
        return res;
    }

    /// @note  Aliasing safe — oVec may alias iVec1 or iVec2.
    void sub(const ShareVector<ELL>& iVec1, const ShareVector<ELL>& iVec2, ShareVector<ELL>& oVec)
    {
        RVECTOR<ELL>::sub(iVec1.thisShare, iVec2.thisShare, oVec.thisShare);
        RVECTOR<ELL>::sub(iVec1.nxtShare, iVec2.nxtShare, oVec.nxtShare);
    }

    ShareScalar mul(const ShareScalar num1, const ShareScalar num2)
    {
        uint8_t mulRes33 = math::ringMul8<ELL>(num1.thisShare, num2.thisShare);
        mulRes33 = math::ringAdd8<ELL>(mulRes33, math::ringMul8<ELL>(num1.thisShare, num2.nxtShare));
        mulRes33 = math::ringAdd8<ELL>(mulRes33, math::ringMul8<ELL>(num1.nxtShare, num2.thisShare));
        mulRes33 = math::ringAdd8<ELL>(mulRes33, crng<ELL>());

        ShareScalar mulRes;
        mulRes.thisShare = mulRes33;

        nioToPrev_->send_data(&mulRes.thisShare, 1);
        nioToPrev_->flush();
        nioToNext_->recv_data(&mulRes.nxtShare, 1);

        return mulRes;
    }

    /// @pre  &oVec != &iVec1 && &oVec != &iVec2
    void
    hadamard(const ShareVector<ELL>& iVec1, const ShareVector<ELL>& iVec2, ShareVector<ELL>& oVec)
    {
        if (&oVec == &iVec1 || &oVec == &iVec2)
        {
            throw std::invalid_argument("hadamard: oVec must not alias iVec1 or iVec2");
        }

        // oVec.thisShare = i1.s1 ⊙ i2.s1
        RVECTOR<ELL>::hadamard(iVec1.thisShare, iVec2.thisShare, oVec.thisShare);

        // oVec.thisShare += i1.s1 ⊙ i2.s2
        RVECTOR<ELL>::hadamard(iVec1.thisShare, iVec2.nxtShare, oVec.nxtShare);
        RVECTOR<ELL>::add(oVec.thisShare, oVec.nxtShare, oVec.thisShare);

        // oVec.thisShare += i1.s2 ⊙ i2.s1
        RVECTOR<ELL>::hadamard(iVec1.nxtShare, iVec2.thisShare, oVec.nxtShare);
        RVECTOR<ELL>::add(oVec.thisShare, oVec.nxtShare, oVec.thisShare);

        // oVec.thisShare += crng
        crng<ELL>(oVec.nxtShare);
        RVECTOR<ELL>::add(oVec.thisShare, oVec.nxtShare, oVec.thisShare);

        // Reshare: send oVec.thisShare, recv into oVec.nxtShare
        _reshare_ring(oVec.thisShare.data(), oVec.nxtShare.data(),
                      oVec.nxtShare.bytesSize());
    }

    ShareScalar dot(const ShareVector<ELL>& iVec1, const ShareVector<ELL>& iVec2)
    {
        ShareScalar dotRes;

        uint8_t dotVal = RVECTOR<ELL>::dot(iVec1.thisShare, iVec2.thisShare);
        dotVal = math::ringAdd8<ELL>(dotVal, RVECTOR<ELL>::dot(iVec1.thisShare, iVec2.nxtShare));
        dotVal = math::ringAdd8<ELL>(dotVal, RVECTOR<ELL>::dot(iVec1.nxtShare, iVec2.thisShare));

        dotRes.thisShare = math::ringAdd8<ELL>(dotVal, crng<ELL>());

        nioToPrev_->send_data(&dotRes.thisShare, 1);
        nioToPrev_->flush();
        nioToNext_->recv_data(&dotRes.nxtShare, 1);

        return dotRes;
    }

    template <uint64_t ELL_TO>
        requires(ELL == 1 && ELL_TO >= 2 && ELL_TO <= 6)
    ShareScalar ringConv(const ShareScalar num)
    {
        const uint8_t b0 = num.thisShare; // 0 or 1
        const uint8_t b1 = num.nxtShare;  // 0 or 1

        //  Round 1: Jprod01 = Jb0 ⊙ Jb1
        uint8_t m1;
        if constexpr (PID == ShrRep3Pid::P0)
        {
            const uint8_t prod01 = math::ringMul8<ELL_TO>(b0, b1);
            m1 = math::ringAdd8<ELL_TO>(prod01, crng<ELL_TO>());
        }
        else
        {
            m1 = crng<ELL_TO>();
        }
        nioToPrev_->send_data(&m1, 1);
        nioToPrev_->flush();

        ShareScalar Jprod01;
        Jprod01.thisShare = m1;
        nioToNext_->recv_data(&Jprod01.nxtShare, 1);

        // Jd = Jb0 + Jb1 - 2·Jprod01
        ShareScalar Jd;
        if constexpr (PID == ShrRep3Pid::P0)
        {
            Jd.thisShare = math::ringSub8<ELL_TO>(
                b0, math::ringAdd8<ELL_TO>(Jprod01.thisShare, Jprod01.thisShare)
            );
            Jd.nxtShare = math::ringSub8<ELL_TO>(
                b1, math::ringAdd8<ELL_TO>(Jprod01.nxtShare, Jprod01.nxtShare)
            );
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            Jd.thisShare = math::ringSub8<ELL_TO>(
                b0, math::ringAdd8<ELL_TO>(Jprod01.thisShare, Jprod01.thisShare)
            );
            Jd.nxtShare = math::ringSub8<ELL_TO>(
                uint8_t{0}, math::ringAdd8<ELL_TO>(Jprod01.nxtShare, Jprod01.nxtShare)
            );
        }
        else // PID == P2
        {
            Jd.thisShare = math::ringSub8<ELL_TO>(
                uint8_t{0}, math::ringAdd8<ELL_TO>(Jprod01.thisShare, Jprod01.thisShare)
            );
            Jd.nxtShare = math::ringSub8<ELL_TO>(
                b1, math::ringAdd8<ELL_TO>(Jprod01.nxtShare, Jprod01.nxtShare)
            );
        }

        //  Round 2: Jprod2 = Jd ⊙ Jb2,  then Je = Jd + Jb2 - 2·Jprod2
        uint8_t m2;
        if constexpr (PID == ShrRep3Pid::P0)
        {
            m2 = crng<ELL_TO>();
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            const uint8_t prod2 = math::ringMul8<ELL_TO>(Jd.thisShare, b1);
            m2 = math::ringAdd8<ELL_TO>(prod2, crng<ELL_TO>());
        }
        else // PID == P2
        {
            const uint8_t sum = math::ringAdd8<ELL_TO>(Jd.thisShare, Jd.nxtShare);
            const uint8_t prod2 = math::ringMul8<ELL_TO>(sum, b0);
            m2 = math::ringAdd8<ELL_TO>(prod2, crng<ELL_TO>());
        }
        nioToPrev_->send_data(&m2, 1);
        nioToPrev_->flush();

        ShareScalar Jprod2;
        Jprod2.thisShare = m2;
        nioToNext_->recv_data(&Jprod2.nxtShare, 1);

        // Je = Jd + Jb2 - 2·Jprod2
        ShareScalar result;
        if constexpr (PID == ShrRep3Pid::P0)
        {
            result.thisShare = math::ringSub8<ELL_TO>(
                Jd.thisShare, math::ringAdd8<ELL_TO>(Jprod2.thisShare, Jprod2.thisShare)
            );
            result.nxtShare = math::ringSub8<ELL_TO>(
                Jd.nxtShare, math::ringAdd8<ELL_TO>(Jprod2.nxtShare, Jprod2.nxtShare)
            );
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            result.thisShare = math::ringSub8<ELL_TO>(
                Jd.thisShare, math::ringAdd8<ELL_TO>(Jprod2.thisShare, Jprod2.thisShare)
            );

            result.nxtShare = math::ringSub8<ELL_TO>(
                math::ringAdd8<ELL_TO>(Jd.nxtShare, b1),
                math::ringAdd8<ELL_TO>(Jprod2.nxtShare, Jprod2.nxtShare)
            );
        }
        else // PID == P2
        {
            result.thisShare = math::ringSub8<ELL_TO>(
                math::ringAdd8<ELL_TO>(Jd.thisShare, b0),
                math::ringAdd8<ELL_TO>(Jprod2.thisShare, Jprod2.thisShare)
            );
            result.nxtShare = math::ringSub8<ELL_TO>(
                Jd.nxtShare, math::ringAdd8<ELL_TO>(Jprod2.nxtShare, Jprod2.nxtShare)
            );
        }

        return result;
    }

    template <uint64_t ELL_TO>
        requires(ELL == 1 && ELL_TO >= 2 && ELL_TO <= 6)
    void ringConv(const ShareVector<ELL>& iVec, ShareVector<ELL_TO>& oVec)
    {
        const size_t n = iVec.thisShare.size();
        if (n == 0)
        {
            return;
        }

        // Lift b0, b1 from Z_2 to Z_{2^ELL_TO} into oVec.
        for (size_t i = 0; i < n; ++i)
        {
            oVec.thisShare.set(i, iVec.thisShare.get(i));
            oVec.nxtShare.set(i, iVec.nxtShare.get(i));
        }

        RVECTOR<ELL_TO> prod01(n);
        RVECTOR<ELL_TO> tmp(n);

        // Round 1 – Jprod01 = Jb0 ⊙ Jb1  →  Jd = Jb0 + Jb1 - 2·Jprod01
        if constexpr (PID == ShrRep3Pid::P0)
        {
            RVECTOR<ELL_TO>::hadamard(oVec.thisShare, oVec.nxtShare, prod01);
            crng<ELL_TO>(tmp);
            RVECTOR<ELL_TO>::add(prod01, tmp, prod01);
        }
        else
        {
            prod01.fill();
            crng<ELL_TO>(tmp);
            RVECTOR<ELL_TO>::add(prod01, tmp, prod01);
        }
        {
            _reshare_ring(prod01.data(), tmp.data(), prod01.bytesSize());
        }

        RVECTOR<ELL_TO>::add(prod01, prod01, prod01);
        RVECTOR<ELL_TO>::add(tmp, tmp, tmp);
        if constexpr (PID == ShrRep3Pid::P0)
        {
            RVECTOR<ELL_TO>::sub(oVec.thisShare, prod01, oVec.thisShare);
            RVECTOR<ELL_TO>::sub(oVec.nxtShare, tmp, oVec.nxtShare);
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            RVECTOR<ELL_TO>::sub(oVec.thisShare, prod01, oVec.thisShare);
            oVec.nxtShare.fill();
            RVECTOR<ELL_TO>::sub(oVec.nxtShare, tmp, oVec.nxtShare);
        }
        else // PID == P2
        {
            oVec.thisShare.fill();
            RVECTOR<ELL_TO>::sub(oVec.thisShare, prod01, oVec.thisShare);
            RVECTOR<ELL_TO>::sub(oVec.nxtShare, tmp, oVec.nxtShare);
        }

        // Round 2 – Jprod2 = Jd ⊙ Jb2  →  Je = Jd + Jb2 - 2·Jprod2
        if constexpr (PID == ShrRep3Pid::P0)
        {
            prod01.fill();
            crng<ELL_TO>(tmp);
            RVECTOR<ELL_TO>::add(prod01, tmp, prod01);
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            for (size_t i = 0; i < n; ++i)
            {
                prod01.set(i, math::ringMul8<ELL_TO>(oVec.thisShare.get(i), iVec.nxtShare.get(i)));
            }
            crng<ELL_TO>(tmp);
            RVECTOR<ELL_TO>::add(prod01, tmp, prod01);
        }
        else // PID == P2
        {
            for (size_t i = 0; i < n; ++i)
            {
                uint8_t sum = math::ringAdd8<ELL_TO>(oVec.thisShare.get(i), oVec.nxtShare.get(i));

                prod01.set(i, math::ringMul8<ELL_TO>(sum, iVec.thisShare.get(i)));
            }
            crng<ELL_TO>(tmp);
            RVECTOR<ELL_TO>::add(prod01, tmp, prod01);
        }
        {
            _reshare_ring(prod01.data(), tmp.data(), prod01.bytesSize());
        }

        RVECTOR<ELL_TO>::add(prod01, prod01, prod01);
        RVECTOR<ELL_TO>::add(tmp, tmp, tmp);
        if constexpr (PID == ShrRep3Pid::P0)
        {
            RVECTOR<ELL_TO>::sub(oVec.thisShare, prod01, oVec.thisShare);
            RVECTOR<ELL_TO>::sub(oVec.nxtShare, tmp, oVec.nxtShare);
        }
        else if constexpr (PID == ShrRep3Pid::P1)
        {
            RVECTOR<ELL_TO>::sub(oVec.thisShare, prod01, oVec.thisShare);
            for (size_t i = 0; i < n; ++i)
            {
                oVec.nxtShare.set(
                    i,
                    math::ringSub8<ELL_TO>(
                        math::ringAdd8<ELL_TO>(oVec.nxtShare.get(i), iVec.nxtShare.get(i)),
                        tmp.get(i)
                    )
                );
            }
        }
        else // PID == P2
        {
            for (size_t i = 0; i < n; ++i)
            {
                oVec.thisShare.set(
                    i,
                    math::ringSub8<ELL_TO>(
                        math::ringAdd8<ELL_TO>(oVec.thisShare.get(i), iVec.thisShare.get(i)),
                        prod01.get(i)
                    )
                );
            }
            RVECTOR<ELL_TO>::sub(oVec.nxtShare, tmp, oVec.nxtShare);
        }
    }

    // CRNG — ELL_V selects the target ring explicitly.
    // Scalar cache: a single 128-bit buffer, consumed in bit-sized (1 bit)
    // or byte-sized (8 bits) chunks.  crngIsXor_ tracks how the current
    // cache was computed; a mode switch forces a refill so that each PRF
    // counter value is consumed under exactly one interpretation.
    template <uint64_t ELL_V = ELL> uint8_t crng()
    {
        if constexpr (ELL_V == 1)
        {
            if (crngPtr_ >= BLOCK_CAPACITY * 8 || !crngIsXor_)
            {
                refillCrngCache(true);
                crngPtr_ = 0;
                crngIsXor_ = true;
            }
            uint8_t bit = (reinterpret_cast<uint8_t*>(&crngCache_)[crngPtr_ / 8]
                            >> (crngPtr_ % 8)) & 1;
            ++crngPtr_;
            return bit;
        }
        else
        {
            if (crngPtr_ >= BLOCK_CAPACITY * 8 || crngIsXor_)
            {
                refillCrngCache(false);
                crngPtr_ = 0;
                crngIsXor_ = false;
            }
            uint8_t val = reinterpret_cast<uint8_t*>(&crngCache_)[crngPtr_ / 8]
                          & math::RING_MASK8<ELL_V>;
            crngPtr_ += 8;
            return val;
        }
    }

    template <uint64_t ELL_V = ELL> void crng(RVECTOR<ELL_V>& oVec)
    {
        const size_t n = oVec.size();
        if constexpr (ELL_V == 1)
        {
            auto* data = oVec.data();
            const size_t words = oVec.words();
            size_t w = 0;
            while (w < words)
            {
                alignas(16) emp::block ids[4], rawThis[4], rawPrev[4];
                uint64_t base[2];
                std::memcpy(base, &cRngId_, sizeof(cRngId_));
                for (int k = 0; k < 4; ++k)
                {
                    uint64_t lo = base[0] + k;
                    ids[k] = _mm_set_epi64x(
                        static_cast<int64_t>(base[1] + (lo < base[0] ? 1 : 0)),
                        static_cast<int64_t>(lo)
                    );
                }

                base[0] += 4;
                if (base[0] < 4)
                {
                    ++base[1];
                }

                std::memcpy(&cRngId_, base, sizeof(cRngId_));

                ccrhThis_.H<4>(rawThis, ids);
                ccrhPrev_.H<4>(rawPrev, ids);

                for (int k = 0; k < 4 && w < words; ++k)
                {
                    __m128i raw = _mm_xor_si128(rawThis[k], rawPrev[k]);
                    uint64_t lo = _mm_cvtsi128_si64(raw);
                    uint64_t hi = _mm_cvtsi128_si64(_mm_srli_si128(raw, 8));

                    size_t bits0 = (n - w * 64 < 64) ? (n - w * 64) : static_cast<size_t>(64);
                    if (bits0 < 64)
                    {
                        lo &= (UINT64_C(1) << bits0) - 1;
                    }

                    data[w++] = lo;

                    if (w < words)
                    {
                        size_t bits1 = (n - w * 64 < 64) ? (n - w * 64) : static_cast<size_t>(64);
                        if (bits1 < 64)
                        {
                            hi &= (UINT64_C(1) << bits1) - 1;
                        }

                        data[w++] = hi;
                    }
                }
            }
            crngPtr_ = BLOCK_CAPACITY * 8;
        }
        else
        {
            // H<4> batches 4 ids → 64 correlated bytes per round.
            // SIMD mask + memcpy to oVec.data() — avoids per-element set() overhead.
            constexpr uint8_t M = math::RING_MASK8<ELL_V>;
            const __m128i VM = _mm_set1_epi8(static_cast<char>(M));
            size_t i = 0;
            while (i < n)
            {
                alignas(16) emp::block ids[4], rawThis[4], rawPrev[4];
                uint64_t base[2];
                std::memcpy(base, &cRngId_, sizeof(cRngId_));
                for (int k = 0; k < 4; ++k)
                {
                    uint64_t lo = base[0] + k;
                    ids[k] = _mm_set_epi64x(
                        static_cast<int64_t>(base[1] + (lo < base[0] ? 1 : 0)),
                        static_cast<int64_t>(lo)
                    );
                }

                base[0] += 4;
                if (base[0] < 4)
                {
                    ++base[1];
                }
                std::memcpy(&cRngId_, base, sizeof(cRngId_));

                ccrhThis_.H<4>(rawThis, ids);
                ccrhPrev_.H<4>(rawPrev, ids);

                for (int k = 0; k < 4 && i < n; ++k)
                {
                    __m128i correlated = _mm_sub_epi8(rawThis[k], rawPrev[k]);
                    size_t batch = (n - i < BLOCK_CAPACITY) ? (n - i) : BLOCK_CAPACITY;
                    if constexpr (ELL_V == 8)
                    {
                        std::memcpy(oVec.data() + i, &correlated, batch);
                    }
                    else
                    {
                        __m128i masked = _mm_and_si128(correlated, VM);
                        std::memcpy(oVec.data() + i, &masked, batch);
                    }
                    i += batch;
                }
            }

            crngPtr_ = BLOCK_CAPACITY * 8;
        }
    }

private:
    static constexpr size_t BLOCK_CAPACITY = 16;

    void refillCrngCache(bool isXor)
    {
        __m128i rawThis = ccrhThis_.H(cRngId_);
        __m128i rawPrev = ccrhPrev_.H(cRngId_);

        crngCache_ = isXor ? _mm_xor_si128(rawThis, rawPrev)
                           : _mm_sub_epi8(rawThis, rawPrev);

        uint64_t counter[2];
        std::memcpy(counter, &cRngId_, sizeof(cRngId_));
        if (++counter[0] == 0)
            ++counter[1];
        std::memcpy(&cRngId_, counter, sizeof(cRngId_));
    }

    emp::IOChannel* nioToPrev_, *nioToNext_;
    __m128i cRngId_;
    emp::CCRH ccrhThis_;
    emp::CCRH ccrhPrev_;
    __m128i crngCache_;
    uint8_t crngPtr_;  // bit offset within crngCache_ (0..127)
    bool crngIsXor_ = false;

    // ── Persistent I/O worker threads for _reshare_ring ──
    std::thread sendWorker_, recvWorker_;
    std::atomic<bool> shutdown_{false};

    // Send worker state
    std::mutex sendMtx_;
    std::condition_variable sendCv_;
    const uint8_t* sendBuf_ = nullptr;
    size_t sendTotal_ = 0;
    bool sendReady_ = false;
    bool sendDone_ = false;

    // Recv worker state
    std::mutex recvMtx_;
    std::condition_variable recvCv_;
    uint8_t* recvBuf_ = nullptr;
    size_t recvTotal_ = 0;
    bool recvReady_ = false;
    bool recvDone_ = false;

    // Error propagation
    std::mutex errMtx_;
    std::exception_ptr sendErr_;
    std::exception_ptr recvErr_;

    void sendLoop()
    {
        constexpr size_t CHUNK = 1 << 16;
        while (true)
        {
            {
                std::unique_lock lk(sendMtx_);
                sendCv_.wait(lk, [this] { return sendReady_; });
                if (shutdown_) return;
            }

            try
            {
                auto* snd = sendBuf_;
                size_t total = sendTotal_;
                for (size_t off = 0; off < total; off += CHUNK)
                {
                    size_t n = std::min(CHUNK, total - off);
                    nioToPrev_->send_data(snd + off, n);
                    nioToPrev_->flush();
                }
            }
            catch (...)
            {
                std::lock_guard lk(errMtx_);
                if (!sendErr_) sendErr_ = std::current_exception();
            }

            {
                std::lock_guard lk(sendMtx_);
                sendReady_ = false;
                sendDone_ = true;
            }
            sendCv_.notify_one();
        }
    }

    void recvLoop()
    {
        constexpr size_t CHUNK = 1 << 16;
        while (true)
        {
            {
                std::unique_lock lk(recvMtx_);
                recvCv_.wait(lk, [this] { return recvReady_; });
                if (shutdown_) return;
            }

            try
            {
                auto* rcv = recvBuf_;
                size_t total = recvTotal_;
                for (size_t off = 0; off < total; off += CHUNK)
                {
                    size_t n = std::min(CHUNK, total - off);
                    nioToNext_->recv_data(rcv + off, n);
                }
            }
            catch (...)
            {
                std::lock_guard lk(errMtx_);
                if (!recvErr_) recvErr_ = std::current_exception();
            }

            {
                std::lock_guard lk(recvMtx_);
                recvReady_ = false;
                recvDone_ = true;
            }
            recvCv_.notify_one();
        }
    }
};

} // namespace scucse::crypto

#endif // ABY3_HPP
