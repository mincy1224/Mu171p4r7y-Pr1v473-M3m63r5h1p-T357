#ifndef SHR_REP3_HPP
#define SHR_REP3_HPP

#include <cstdint>
#include <memory>
#include <utility>

#include "rvector.hpp"
#include <emp-tool/runtime/io/io_channel.h>

namespace scucse::crypto
{

    enum class ShrRep3Pid
    {
        P0,
        P1,
        P2,
    };

    /// @brief The first party other than PID.
    template <ShrRep3Pid PID>
    inline constexpr ShrRep3Pid OTHER0 = PID == ShrRep3Pid::P0 ? ShrRep3Pid::P1 : ShrRep3Pid::P0;

    /// @brief The second party other than PID (the remaining one).
    template <ShrRep3Pid PID>
    inline constexpr ShrRep3Pid OTHER1 = PID == ShrRep3Pid::P2 ? ShrRep3Pid::P1 : ShrRep3Pid::P2;

    //  Public share types

    /// @brief Scalar replicated share: (thisShare, nxtShare).
    struct ShrRep3ShareScalar
    {
        uint8_t thisShare;
        uint8_t nxtShare;
    };

    /// @brief Vector replicated share over Z_{2^ELL}.
    template <uint64_t ELL, template <uint64_t> typename RVECTOR> struct ShrRep3ShareVec
    {
        RVECTOR<ELL> thisShare;
        RVECTOR<ELL> nxtShare;

        ShrRep3ShareVec() = default;
        explicit ShrRep3ShareVec(size_t n) : thisShare(n), nxtShare(n)
        {
        }
        size_t size() const
        {
            return thisShare.size();
        }
    };

    /// @brief Replicated secret sharing over Z_{2^ELL}.
    ///
    /// Models the standard 3-party RSS ideal functionalities:
    /// share, receive share, and correlated randomness.
    ///
    /// Each party holds two Net channels, one to each of the other parties.

    template 
    <
        ShrRep3Pid PID,
        uint64_t ELL,
        template <uint64_t> typename RVECTOR,
        template <ShrRep3Pid, uint64_t, template <uint64_t> typename> typename SCHEME
    >
    concept ShrRep3Cnstr =
                        std::constructible_from<
                            SCHEME<PID, ELL, RVECTOR>,
                            emp::IOChannel*,
                            emp::IOChannel*> &&
                        requires(SCHEME<PID, ELL, RVECTOR> scheme, RVECTOR<ELL>& oVec)
                        {
                            { scheme.crng() } -> std::same_as<uint8_t>;
                            { scheme.crng(oVec) } -> std::same_as<void>;
                        } &&
                        requires(
                            SCHEME<PID, ELL, RVECTOR> scheme,
                            const uint8_t num,
                            const RVECTOR<ELL>& iVec,
                            const ShrRep3ShareScalar ss1,
                            const ShrRep3ShareScalar ss2,
                            const ShrRep3ShareVec<ELL, RVECTOR> sv1,
                            const ShrRep3ShareVec<ELL, RVECTOR> sv2,
                            ShrRep3ShareVec<ELL, RVECTOR> oSv,
                            RVECTOR<ELL>& oVecPlain
                        )
                        {
                            { scheme.share(num) } -> std::same_as<ShrRep3ShareScalar>;
                            { scheme.share(iVec, oSv) } -> std::same_as<void>;

                            { scheme.recvShare() } -> std::same_as<ShrRep3ShareScalar>;
                            { scheme.recvShare(oSv) } -> std::same_as<void>;

                            { scheme.add(ss1, ss2) } -> std::same_as<ShrRep3ShareScalar>;
                            { scheme.sub(ss1, ss2) } -> std::same_as<ShrRep3ShareScalar>;
                            { scheme.mul(ss1, ss2) } -> std::same_as<ShrRep3ShareScalar>;
                            { scheme.add(sv1, sv2, oSv) } -> std::same_as<void>;
                            { scheme.sub(sv1, sv2, oSv) } -> std::same_as<void>;
                            { scheme.hadamard(sv1, sv2, oSv) } -> std::same_as<void>;

                            { scheme.revealAll(ss1) } -> std::same_as<uint8_t>;
                            { scheme.revealAll(sv1, oVecPlain) } -> std::same_as<void>;

                            {
                                scheme.template send<OTHER0<PID>>(num)
                            } -> std::same_as<void>;
                            {
                                scheme.template send<OTHER1<PID>>(num)
                            } -> std::same_as<void>;
                            {
                                scheme.template send<OTHER0<PID>>(iVec)
                            } -> std::same_as<void>;
                            {
                                scheme.template send<OTHER1<PID>>(iVec)
                            } -> std::same_as<void>;
                            {
                                scheme.template recv<OTHER0<PID>>()
                            } -> std::same_as<uint8_t>;
                            {
                                scheme.template recv<OTHER1<PID>>()
                            } -> std::same_as<uint8_t>;
                            {
                                scheme.template recv<OTHER0<PID>>(oVecPlain)
                            } -> std::same_as<void>;
                            {
                                scheme.template recv<OTHER1<PID>>(oVecPlain)
                            } -> std::same_as<void>;
                        } &&
                        // ringConv<ELL_TO>: only valid when ELL == 1 (binary RSS → arithmetic RSS),
                        // and ELL_TO must be in [2, 8].
                        ((ELL != 1) ||
                            []<std::size_t... Is>(std::index_sequence<Is...>)
                            {
                                return (
                                    requires(
                                        SCHEME<PID, ELL, RVECTOR> scheme,
                                        const ShrRep3ShareScalar ss,
                                        const ShrRep3ShareVec<ELL, RVECTOR> sv,
                                        ShrRep3ShareVec<Is + 2, RVECTOR> oSv
                                    )
                                    {
                                        {
                                            scheme.template ringConv<Is + 2>(ss)
                                        } -> std::same_as<ShrRep3ShareScalar>;
                                        {
                                            scheme.template ringConv<Is + 2>(sv, oSv)
                                        } -> std::same_as<void>;
                                    } && ...
                                );
                            }(std::make_index_sequence<7>{}));
} // namespace scucse::crypto

#endif // !SHR_REP3_HPP
