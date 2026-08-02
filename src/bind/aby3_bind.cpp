//  @author  mincy
#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

#include "common.hpp"
#include "bind_common.hpp"

namespace nb = nanobind;
using namespace scucse::crypto;

// ———  party id → ShrRep3Pid  mapping  ———

template <int P> struct Rss3Party;

template <> struct Rss3Party<0>
{
    static constexpr ShrRep3Pid value = ShrRep3Pid::P0;
};

template <> struct Rss3Party<1>
{
    static constexpr ShrRep3Pid value = ShrRep3Pid::P1;
};

template <> struct Rss3Party<2>
{
    static constexpr ShrRep3Pid value = ShrRep3Pid::P2;
};

// ———  Rss3T  template wrapper  ———

template <ShrRep3Pid PID, uint64_t ELL> struct Rss3T
{
    static constexpr ShrRep3Pid pid = PID;
    static constexpr uint64_t ell = ELL;
    static constexpr int party_id = static_cast<int>(PID);

    using Aby3Type = bind::SHR_RSS3<PID, ELL>;
    static_assert(ShrRep3Cnstr<PID, ELL, bind::RVECTOR, Aby3>);
    using RvType = bind::RVECTOR<ELL>;
    using ShareVecType = ShrRep3ShareVec<ELL, bind::RVECTOR>;
    using ShareScalar = ShrRep3ShareScalar;

    // shared_ptr keeps channels alive while protocol object exists
    std::shared_ptr<emp::IOChannel> srv_, cli_;
    std::unique_ptr<Aby3Type> aby3_;

    Rss3T() = default;

    /// Construct with pre-built IOChannels (shared ownership).
    Rss3T(std::shared_ptr<emp::IOChannel> srv, std::shared_ptr<emp::IOChannel> cli)
        : srv_(std::move(srv)), cli_(std::move(cli)),
          aby3_(std::make_unique<Aby3Type>(srv_.get(), cli_.get()))
    {
    }

    ~Rss3T() = default;

    // ——— crng  ———

    uint8_t crng()
    {
        return aby3_->template crng<ELL>();
    }

    void crng_vec(RvType& vec)
    {
        aby3_->template crng<ELL>(vec);
    }

    // ——— share  ———

    ShareScalar share_scalar(uint8_t val)
    {
        return aby3_->share(val);
    }

    void share_vec(const RvType& vec, ShareVecType& sv)
    {
        // share writes both fields of oVec; ensure oVec is sized.
        if (sv.thisShare.size() != vec.size())
        {
            sv = ShareVecType(vec.size());
        }
        aby3_->share(vec, sv);
    }

    // ——— recvShare  ———

    ShareScalar recv_share_scalar()
    {
        return aby3_->recvShare();
    }

    void recv_share_vec(ShareVecType& sv)
    {
        aby3_->recvShare(sv);
    }

    // ——— byte counters ———
    uint64_t bytes_sent()     const { return aby3_->bytes_sent(); }
    uint64_t bytes_recv()     const { return aby3_->bytes_recv(); }
    void     clear_send_cnt()       { aby3_->clear_send_cnt(); }
    void     clear_recv_cnt()       { aby3_->clear_recv_cnt(); }

    // ——— flush ———
    void flush() { aby3_->flush(); }

    // ——— send / recv (plain data transfer) ———

    void send(int to, uint8_t val)
    {
        switch (to)
        {
            case 0:
            {
                if constexpr (static_cast<int>(PID) != 0)
                {
                    aby3_->template send<ShrRep3Pid::P0>(val);
                    return;
                }
                throw std::invalid_argument("cannot send to self");
            }
            case 1:
            {
                if constexpr (static_cast<int>(PID) != 1)
                {
                    aby3_->template send<ShrRep3Pid::P1>(val);
                    return;
                }
                throw std::invalid_argument("cannot send to self");
            }
            case 2:
            {
                if constexpr (static_cast<int>(PID) != 2)
                {
                    aby3_->template send<ShrRep3Pid::P2>(val);
                    return;
                }
                throw std::invalid_argument("cannot send to self");
            }
            default:
                throw std::invalid_argument("to must be in {0,1,2}");
        }
    }

    uint8_t recv(int from)
    {
        switch (from)
        {
            case 0:
            {
                if constexpr (static_cast<int>(PID) != 0)
                {
                    return aby3_->template recv<ShrRep3Pid::P0>();
                }
                throw std::invalid_argument("cannot recv from self");
            }
            case 1:
            {
                if constexpr (static_cast<int>(PID) != 1)
                {
                    return aby3_->template recv<ShrRep3Pid::P1>();
                }
                throw std::invalid_argument("cannot recv from self");
            }
            case 2:
            {
                if constexpr (static_cast<int>(PID) != 2)
                {
                    return aby3_->template recv<ShrRep3Pid::P2>();
                }
                throw std::invalid_argument("cannot recv from self");
            }
            default:
                throw std::invalid_argument("from must be in {0,1,2}");
        }
    }

    // ——— raw bytes I/O (internal — used by Python send/recv overloads) ———

    void send_bytes(int to, const uint8_t* data, size_t len)
    {
        if (to < 0 || to > 2 || to == party_id)
            throw std::invalid_argument(
                to == party_id ? "cannot send to self" : "to must be in {0,1,2}");
        aby3_->send_data(to, data, len);
    }

    void recv_bytes(int from, uint8_t* data, size_t len)
    {
        if (from < 0 || from > 2 || from == party_id)
            throw std::invalid_argument(
                from == party_id ? "cannot recv from self" : "from must be in {0,1,2}");
        aby3_->recv_data(from, data, len);
    }

    // ——— revealAll  ———

    uint32_t revealAll(const ShareScalar& ss)
    {
        return aby3_->revealAll(ss);
    }

    void revealAll(const ShareVecType& sv, RvType& out)
    {
        aby3_->revealAll(sv, out);
    }

    // ——— add  ———

    ShareScalar add_scalar(ShareScalar a, ShareScalar b)
    {
        return aby3_->add(a, b);
    }

    void add_vec(const ShareVecType& sv1, const ShareVecType& sv2, ShareVecType& out)
    {
        aby3_->add(sv1, sv2, out);
    }

    // ——— sub  ———

    ShareScalar sub_scalar(ShareScalar a, ShareScalar b)
    {
        return aby3_->sub(a, b);
    }

    void sub_vec(const ShareVecType& sv1, const ShareVecType& sv2, ShareVecType& out)
    {
        aby3_->sub(sv1, sv2, out);
    }

    // ——— mul / hadamard  ———

    ShareScalar mul_scalar(ShareScalar a, ShareScalar b)
    {
        return aby3_->mul(a, b);
    }

    void hadamard_vec(const ShareVecType& sv1, const ShareVecType& sv2, ShareVecType& out)
    {
        if (out.size() != sv1.size())
        {
            out = ShareVecType(sv1.size());
        }
        aby3_->hadamard(sv1, sv2, out);
    }

    // ——— dot  ———

    ShareScalar dot_vec(const ShareVecType& sv1, const ShareVecType& sv2)
    {
        return aby3_->dot(sv1, sv2);
    }

    // ——— ringConv  (only when ELL == 1)  ———

    ShareScalar ring_conv_scalar(ShareScalar ss, uint64_t ell_to)
        requires(ELL == 1)
    {
        switch (ell_to)
        {
            case 2:
                return aby3_->template ringConv<2>(ss);
            case 3:
                return aby3_->template ringConv<3>(ss);
            case 4:
                return aby3_->template ringConv<4>(ss);
            case 5:
                return aby3_->template ringConv<5>(ss);
            case 6:
                return aby3_->template ringConv<6>(ss);
            default:
                throw std::invalid_argument("ell_to must be in [2, 6]");
        }
    }

    void ring_conv_vec(const ShareVecType& sv, nb::object sv_out, uint64_t ell_to)
        requires(ELL == 1)
    {
        switch (ell_to)
        {
            case 2:
            {
                auto& out = nb::cast<ShrRep3ShareVec<2, bind::RVECTOR>&>(sv_out);
                aby3_->template ringConv<2>(sv, out);
                break;
            }
            case 3:
            {
                auto& out = nb::cast<ShrRep3ShareVec<3, bind::RVECTOR>&>(sv_out);
                aby3_->template ringConv<3>(sv, out);
                break;
            }
            case 4:
            {
                auto& out = nb::cast<ShrRep3ShareVec<4, bind::RVECTOR>&>(sv_out);
                aby3_->template ringConv<4>(sv, out);
                break;
            }
            case 5:
            {
                auto& out = nb::cast<ShrRep3ShareVec<5, bind::RVECTOR>&>(sv_out);
                aby3_->template ringConv<5>(sv, out);
                break;
            }
            case 6:
            {
                auto& out = nb::cast<ShrRep3ShareVec<6, bind::RVECTOR>&>(sv_out);
                aby3_->template ringConv<6>(sv, out);
                break;
            }
            default:
                throw std::invalid_argument("ell_to must be in [2, 6]");
        }
    }

};

// ———  bind helpers  ———

// Lookup tables
static nb::object rss3_types[8 + 1][3]; // [ell][party]
static nb::object sv_types[8 + 1];      // [ell]

// Bind ShareVec class for one ELL (used by ringConv dispatch too)
template <uint64_t ELL> static void bind_rss3_share_vec(nb::module_& m, const char* name)
{
    using namespace nb::literals;
    using SV = ShrRep3ShareVec<ELL, bind::RVECTOR>;

    nb::class_<SV>(m, name)
        .def(nb::init<size_t>(), "size"_a, "Allocate a zero-initialised share vector of given size")
        .def_prop_rw("this_share",
            [](const SV& sv) -> const bind::RVECTOR<ELL>& { return sv.thisShare; },
            [](SV& sv, const bind::RVECTOR<ELL>& v) {
                if (v.size() != sv.nxtShare.size())
                    throw std::invalid_argument(
                        "this_share size (" + std::to_string(v.size())
                        + ") must match nxt_share size (" + std::to_string(sv.nxtShare.size()) + ")");
                sv.thisShare = v;
            },
            "First RSS component  (Rvector)")
        .def_prop_rw("nxt_share",
            [](const SV& sv) -> const bind::RVECTOR<ELL>& { return sv.nxtShare; },
            [](SV& sv, const bind::RVECTOR<ELL>& v) {
                if (v.size() != sv.thisShare.size())
                    throw std::invalid_argument(
                        "nxt_share size (" + std::to_string(v.size())
                        + ") must match this_share size (" + std::to_string(sv.thisShare.size()) + ")");
                sv.nxtShare = v;
            },
            "Second RSS component (Rvector)")
        .def_prop_ro("size", &SV::size);

    sv_types[ELL] = m.attr(name);
}

template <ShrRep3Pid PID, uint64_t ELL> static void bind_rss3_instance(nb::module_& m)
{
    using namespace nb::literals;
    using T = Rss3T<PID, ELL>;
    static constexpr int P = static_cast<int>(PID);

    char name[64];
    std::snprintf(name, sizeof(name), "ShrRep3_%lu_%d", ELL, P);

    auto cls = nb::class_<T>(m, name);

    // ——— constructor (persistent channels) ———
    //
    //  Takes two Python Channel objects.  acquire() extracts the IOChannel*
    //  raw pointers; the protocol borrows them for its lifetime.  The Channel
    //  objects stay in the worker's ``channels`` dict — the protocol does NOT
    //  hold Python references (avoids nanobind destructor-interaction issues).
    //
    cls.def(
        "__init__",
        [](T* self, nb::object srv_channel, nb::object cli_channel)
        {
            auto srv = bind::netio_acquire(
                nb::cast<uintptr_t>(srv_channel.attr("acquire")()));
            auto cli = bind::netio_acquire(
                nb::cast<uintptr_t>(cli_channel.attr("acquire")()));
            new (self) T(std::move(srv), std::move(cli));
        },
        "srv_channel"_a,
        "cli_channel"_a,
        "Construct an Rss3 party from persistent channels.\n"
        "  srv_channel  — Channel for communication with PREV party\n"
        "  cli_channel  — Channel for communication with NEXT party"
    );

    // ——— properties ———
    cls.def_prop_ro("ell", [](const T&) { return ELL; });
    cls.def_prop_ro("party", [](const T&) { return P; });

    // ——— crng ———
    cls.def("crng", &T::crng, "Return a correlated-random byte in Z_{2^ELL}");
    cls.def("crng_vec", &T::crng_vec, "vec"_a, "Fill vec with correlated randomness");

    // ——— share_scalar ———
    cls.def("share_scalar", &T::share_scalar, "val"_a, "Share a scalar → (thisShare, nxtShare)");
    cls.def(
        "share_vector",
        [](T& self, const typename T::RvType& vec, typename T::ShareVecType& sv,
           math::RvectorPack&) { self.share_vec(vec, sv); },
        nb::call_guard<nb::gil_scoped_release>(),
        "vec"_a, "sv"_a, "auxBuf"_a, "Share a vector → writes into sv; auxBuf is scratch space"
    );

    // ——— recv_scalar_share ———
    cls.def("recv_scalar_share", &T::recv_share_scalar, "Receive a scalar share via reshare");
    cls.def(
        "recv_vector_share",
        [](T& self, typename T::ShareVecType& sv, math::RvectorPack&) { self.recv_share_vec(sv); },
        nb::call_guard<nb::gil_scoped_release>(),
        "sv"_a, "auxBuf"_a, "Receive a vector share via reshare → writes into sv; auxBuf is scratch space"
    );

    // ——— send_data / recv_data — scalar + byte array (no length prefix) ———
    cls.def("send_data", &T::send, "to_pid"_a, "val"_a, "Send a scalar (uint8_t) to party 'to_pid'");
    cls.def(
        "send_data",
        [](T& self, int to, nb::object data) {
            auto [ptr, len] = bind::_getByteBuffer(data);
            {
                nb::gil_scoped_release release;
                self.send_bytes(to, reinterpret_cast<const uint8_t*>(ptr), len);
            }
        },
        "to_pid"_a, "data"_a, "Send raw bytes (no length prefix) to party 'to_pid'"
    );
    cls.def("recv_data", &T::recv, "from_pid"_a, "Receive a scalar (uint8_t) from party 'from_pid'");
    cls.def(
        "recv_data",
        [](T& self, int from, nb::object buf) {
            auto [ptr, len] = bind::_getWritableByteBuffer(buf);
            {
                nb::gil_scoped_release release;
                self.recv_bytes(from, reinterpret_cast<uint8_t*>(ptr), len);
            }
        },
        "from_pid"_a, "buf"_a, "Receive raw bytes (no length prefix) from party 'from_pid' into pre-allocated buf"
    );

    // ——— reveal ———
    cls.def(
        "reveal_scalar",
        nb::overload_cast<const typename T::ShareScalar&>(&T::revealAll),
        "ss"_a,
        "Reveal a scalar share to all three parties.  P1 sends the missing share to P0;\n"
        "  P0 reconstructs and broadcasts.  All three return the plaintext."
    );
    cls.def(
        "reveal_vector",
        [](T& self, const typename T::ShareVecType& sv, typename T::RvType& out,
           math::RvectorPack&) { self.revealAll(sv, out); },
        nb::call_guard<nb::gil_scoped_release>(),
        "sv"_a,
        "out"_a,
        "auxBuf"_a,
        "Reveal a vector share to all three parties.  out must be pre-allocated to the\n"
        "  correct size.  Filled with the reconstructed plaintext.  auxBuf is scratch space."
    );

    // ——— byte counters ———
    cls.def("bytes_sent",       &T::bytes_sent,     "Total bytes sent over both channels");
    cls.def("bytes_recv",       &T::bytes_recv,     "Total bytes received over both channels");
    cls.def("clear_send_cnt",   &T::clear_send_cnt, "Reset sent-byte counter to zero");
    cls.def("clear_recv_cnt",   &T::clear_recv_cnt, "Reset received-byte counter to zero");
    cls.def("flush",            &T::flush,          "Flush both underlying NetIO send buffers");

    // ——— add ———
    cls.def("add", &T::add_scalar, "a"_a, "b"_a, "Add two scalar shares → (thisShare, nxtShare)");
    cls.def(
        "add_vec",
        &T::add_vec,
        "sv1"_a,
        "sv2"_a,
        "out"_a,
        "Add two vector shares → out (aliasing safe)"
    );

    // ——— sub ———
    cls.def(
        "sub", &T::sub_scalar, "a"_a, "b"_a, "Subtract two scalar shares → (thisShare, nxtShare)"
    );
    cls.def(
        "sub_vec",
        &T::sub_vec,
        "sv1"_a,
        "sv2"_a,
        "out"_a,
        "Subtract two vector shares → out (aliasing safe)"
    );

    // ——— mul / hadamard ———
    cls.def(
        "mul",
        &T::mul_scalar,
        nb::call_guard<nb::gil_scoped_release>(),
        "a"_a,
        "b"_a,
        "Multiply two scalar shares (network round) → (thisShare, nxtShare)"
    );
    cls.def(
        "hadamard",
        &T::hadamard_vec,
        nb::call_guard<nb::gil_scoped_release>(),
        "sv1"_a,
        "sv2"_a,
        "out"_a,
        "Element-wise multiply → out (out must not alias inputs)"
    );

    // ——— dot ———
    cls.def(
        "dot",
        &T::dot_vec,
        nb::call_guard<nb::gil_scoped_release>(),
        "sv1"_a,
        "sv2"_a,
        "Dot product of two share vectors → (thisShare, nxtShare)"
    );

    // ——— ringConv  (only ELL == 1) ———
    if constexpr (ELL == 1)
    {
        cls.def(
            "ring_conv",
            &T::ring_conv_scalar,
            nb::call_guard<nb::gil_scoped_release>(),
            "ss"_a,
            "ell_to"_a,
            "Binary→arithmetic ring conversion (scalar)"
        );
        cls.def(
            "ring_conv_vec",
            &T::ring_conv_vec,
            "sv"_a,
            "sv_out"_a.noconvert(),
            "ell_to"_a,
            "Binary→arithmetic ring conversion (vector)"
        );
    }

    // Store in lookup table
    rss3_types[ELL][P] = m.attr(name);
}

// ———  module entry  ———

void bind_shr_rss3(nb::module_& m)
{
    using namespace nb::literals;

    //  Register ShrRep3ShareScalar  (scalar share type)
    nb::class_<ShrRep3ShareScalar>(m, "ShrRep3ShareScalar")
        .def(nb::init<>())
        .def_rw("this_share", &ShrRep3ShareScalar::thisShare, "First RSS component")
        .def_rw("nxt_share", &ShrRep3ShareScalar::nxtShare, "Second RSS component");

    //  Register ShareVec types  (needed by ring_conv_vec dispatch)
    bind::for_range<bind::RSS3_ELL_MIN, bind::RSS3_ELL_MAX>(
        [&]<uint64_t E>()
        {
            char svname[64];
            std::snprintf(svname, sizeof(svname), "ShrRep3ShareVec%lu", E);
            bind_rss3_share_vec<E>(m, svname);
        }
    );

    //  Register all  ELL × party  classes
    bind::for_range<bind::RSS3_ELL_MIN, bind::RSS3_ELL_MAX>(
        [&]<uint64_t E>()
        {
            bind_rss3_instance<ShrRep3Pid::P0, E>(m);
            bind_rss3_instance<ShrRep3Pid::P1, E>(m);
            bind_rss3_instance<ShrRep3Pid::P2, E>(m);
        }
    );

    //  Factory: ShrRep3(ell, party) → class
    m.def(
        "ShrRep3",
        [](uint64_t ell, int party) -> nb::object
        {
            if (ell < bind::RSS3_ELL_MIN || ell > bind::RSS3_ELL_MAX)
            {
                throw std::invalid_argument(
    "ell out of range [" + std::to_string(bind::RSS3_ELL_MIN)
    + ", " + std::to_string(bind::RSS3_ELL_MAX) + "]");
            }
            if (party < 0 || party > 2)
            {
                throw std::invalid_argument("party must be 0, 1, or 2");
            }
            return rss3_types[ell][party];
        },
        "ell"_a,
        "party"_a,
        "Return the ShrRep3 class for the given (ell, party)."
    );

    //  Factory: ShrRep3ShareVec(ell) → class
    m.def(
        "ShrRep3ShareVec",
        [](uint64_t ell) -> nb::object
        {
            if (ell < bind::RSS3_ELL_MIN || ell > bind::RSS3_ELL_MAX)
            {
                throw std::invalid_argument(
    "ell out of range [" + std::to_string(bind::RSS3_ELL_MIN)
    + ", " + std::to_string(bind::RSS3_ELL_MAX) + "]");
            }
            return sv_types[ell];
        },
        "ell"_a,
        "Return the Rss3ShareVec class for the given ell."
    );
}
